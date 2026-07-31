#!/usr/bin/env python3
"""
fetch_sources.py — the deterministic half of the watch.

Reads sources.yaml, decides which sources are due, fetches them, extracts the
meaningful body text, diffs it against the stored baseline, and writes the
new/changed chunks to state/new_items.json. No translation or scoring happens
here — that's the routine's Claude session, which reads new_items.json next.

Per-source scheduling and notification tiers (all optional, sane defaults):

  check: daily | twice_daily | weekly | <hours>
      How often the cheap hash-diff runs. Hashing is nearly free, so the
      default is daily and there is rarely a reason to slow it down.
  analyze: on_change | never
      on_change (default) hands changed text to the model. `never` tracks the
      hash silently — the change is recorded in the ledger but no model call
      and no finding is produced.
  analyze_from: YYYY-MM-DD
      Stay silent (analyze: never) until this date, then start producing
      findings. Use this INSTEAD of dormant_until for anything that matters
      during the walk, so the source is exercised for months beforehand and a
      dead URL surfaces early rather than on the trail.
  notify: quiet | alert
      quiet (default) = it lands in the report only. alert = it also feeds the
      push digest and is flagged prominently.
  notify_from: YYYY-MM-DD
      On/after this date, quiet escalates to alert. Escalation is one-way.
  dormant_until: YYYY-MM-DD    skip entirely before this date
  stop_after: YYYY-MM-DD       stop checking after this date

The ledger state/sources.json records last_checked, last_hash and
last_change_seen per source (migrated from the older source_state.json on
first run).

Run from the repo root:  python scripts/fetch_sources.py
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from datetime import date, datetime, timedelta, timezone

import requests
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(ROOT, "sources.yaml")
STATE_DIR = os.path.join(ROOT, "state")
CACHE_DIR = os.path.join(STATE_DIR, "cache")
LEDGER_FILE = os.path.join(STATE_DIR, "sources.json")
LEGACY_STATE_FILE = os.path.join(STATE_DIR, "source_state.json")
NEW_FILE = os.path.join(STATE_DIR, "new_items.json")
ITINERARY_FILE = os.path.join(ROOT, "data", "itinerary.json")
HEALTH_FILE = os.path.join(STATE_DIR, "health.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es,en;q=0.9,eu;q=0.8,ca;q=0.7",
}
MAX_CHARS = 6000  # cap per chunk handed to the model
# Ceiling on items handed to one routine session. At MAX_CHARS each, 25 items is
# ~150 KB of text to translate and score — a comfortable load. Overflow is
# DEFERRED to the next run, never dropped: 8 province weather feeds waking in
# April 2027 could otherwise hand a single session 30+ items on a stormy day.
MAX_ITEMS_PER_RUN = 25

# Cadence -> minimum hours between checks. Deliberately under the nominal
# interval so a run firing slightly early doesn't skip a source.
CADENCE_HOURS = {"twice_daily": 10, "daily": 20, "weekly": 156}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sid(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def cache_path(key: str, kind: str = "html") -> str:
    """Path of the cached raw body for a source.

    NOTE: state/cache/ is gitignored and the routine's container is ephemeral, so
    this cache does NOT survive between scheduled runs — it only helps repeated
    local runs. Change history lives in the committed ledger's `extracted`, not
    here. (An earlier `-prev.txt` companion file was written twice per run and
    never read once; it is gone.)
    """
    ext = "pdf" if kind == "pdf" else "html"
    return os.path.join(CACHE_DIR, f"{key}-latest.{ext}")


# alsa.es redirects a cookieless client to itself indefinitely, so every fetch
# shares one cookie jar.
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def fetch_raw(url: str, key: str, kind: str) -> tuple[bytes | None, str | None]:
    """Fetch URL as bytes; cache on success, fall back to the cached copy.

    Returns (data, error_message). The error text is returned rather than only
    printed so the ledger can record why a source is failing.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    raw_path = cache_path(key, kind)
    try:
        r = SESSION.get(url, timeout=45)
        r.raise_for_status()
        if kind != "pdf":
            # aemet.es serves ISO-8859-15; honour the declared charset and only
            # fall back to sniffing when the header omits one.
            if "charset" not in r.headers.get("content-type", "").lower():
                r.encoding = r.apparent_encoding or r.encoding
            data = r.text.encode("utf-8", "replace")
        else:
            data = r.content
        with open(raw_path, "wb") as fh:
            fh.write(data)
        return data, None
    except Exception as exc:
        if os.path.exists(raw_path):
            print(f"  ! fetch failed ({exc}); using cached copy")
            with open(raw_path, "rb") as fh:
                return fh.read(), None
        print(f"  ! fetch failed: {exc}")
        return None, str(exc)[:200]


def extract_pdf_text(data: bytes) -> str:
    """Text layer of a PDF, or '' if unavailable."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "") for p in reader.pages]
        return "\n".join(" ".join(p.split()) for p in pages if p.strip()).strip()
    except Exception as exc:
        print(f"  · pdf text extraction unavailable ({exc}); hashing bytes")
        return ""


def as_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def min_interval_hours(src: dict) -> float:
    """Hours that must elapse before re-checking this source."""
    cadence = src.get("check", "daily")
    if isinstance(cadence, (int, float)):
        return float(cadence)
    return float(CADENCE_HOURS.get(str(cadence).strip(), CADENCE_HOURS["daily"]))


def effective_analyze(src: dict, today: date) -> str:
    """on_change | never, honouring a one-way analyze_from date.

    This is what lets a source be *exercised* long before it is allowed to
    generate findings. Sources that only matter during the walk used to sit behind
    `dormant_until`, which meant their first-ever fetch — URL still valid?
    encoding? parseable? — happened inside the window they existed to serve. Now
    they run silently from today and start producing findings on analyze_from.
    """
    mode = str(src.get("analyze", "on_change")).strip()
    start = as_date(src.get("analyze_from"))
    if start:
        return "on_change" if today >= start else "never"
    return mode if mode in ("on_change", "never") else "on_change"


def effective_notify(src: dict, today: date) -> str:
    """quiet | alert, honouring the one-way notify_from escalation."""
    level = str(src.get("notify", "quiet")).strip()
    escalate_on = as_date(src.get("notify_from"))
    if escalate_on and today >= escalate_on:
        return "alert"
    return level if level in ("quiet", "alert") else "quiet"


def due_reason(src: dict, entry: dict, today: date, now: datetime) -> str | None:
    """None if the source should be checked, else a short skip reason."""
    dormant_until = as_date(src.get("dormant_until"))
    if dormant_until and today < dormant_until:
        return f"dormant until {dormant_until.isoformat()}"

    stop_after = as_date(src.get("stop_after"))
    if stop_after and today > stop_after:
        return f"stopped after {stop_after.isoformat()}"

    last_checked = entry.get("last_checked")
    if last_checked:
        try:
            previous = datetime.fromisoformat(last_checked)
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
            wait = timedelta(hours=min_interval_hours(src))
            if now - previous < wait:
                due_in = wait - (now - previous)
                hours = due_in.total_seconds() / 3600
                return f"checked {round((now - previous).total_seconds() / 3600, 1)}h ago, due in {hours:.1f}h"
        except ValueError:
            pass
    return None


def extract_main_text(html: str, url: str) -> tuple[str, str]:
    """Main body text, boilerplate removed, so we diff on content not chrome.

    Returns (text, extractor). The extractor NAME matters as much as the text:
    trafilatura and BeautifulSoup produce completely different shapes for the
    same page (trafilatura keeps newlines/tabs and drops the <title>; the bs4
    fallback single-spaces everything and includes it). Diffing one against the
    other reports the whole page as new, which is how this monitor manufactured
    seven relevance-100 "accommodation changed" findings from one unchanged page.
    So callers must compare like with like.
    """
    try:
        import trafilatura

        text = trafilatura.extract(html, url=url, favor_recall=True,
                                   include_comments=True)
        if text and len(text.strip()) > 40:
            return text.strip(), "trafilatura"
    except Exception:
        pass
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        return " ".join(soup.get_text(" ").split()), "bs4"
    except Exception:
        return "", ""


def norm_for_hash(text: str) -> str:
    """Collapse cosmetic whitespace so formatting jitter can't flip the hash."""
    return " ".join(text.split())


def added_text(old: str, new: str) -> str:
    if not old:
        return new
    import difflib

    added = [
        ln[1:]  # strip the single leading "+" unified_diff adds
        for ln in difflib.unified_diff(old.splitlines(), new.splitlines(),
                                       n=0, lineterm="")
        if ln.startswith("+") and not ln.startswith("+++")
    ]
    return "\n".join(a for a in added if a.strip())


def stage_window(stages: list[int], itinerary: dict) -> tuple[date, date] | None:
    """Dates a source covering `stages` is worth analyzing, generously padded.

    Eight province weather feeds is the wrong granularity for a linear walk — a
    Barcelona warning is noise on day two in Gipuzkoa. This narrows each to the
    stretch it actually covers.

    Padding is deliberately ASYMMETRIC and grows with stage number, because slip
    accrues: a split or rest day early pushes every later stage later. The planner
    flags 4 stages for splitting, which alone consume all slack, so the late margin
    has to widen along the route rather than being a flat constant.
    """
    if not stages:
        return None
    by_stage = {s["stage"]: s for s in itinerary.get("stages", [])}
    pad = itinerary.get("_padding", {})
    early = int(pad.get("early_days", 10))
    late_base = int(pad.get("late_base_days", 7))
    late_per = float(pad.get("late_per_stage", 0.6))

    first, last = min(stages), max(stages)
    if first not in by_stage or last not in by_stage:
        return None
    start = as_date(by_stage[first]["nominal_date"])
    end = as_date(by_stage[last]["nominal_date"])
    if start is None or end is None:
        return None
    return (start - timedelta(days=early),
            end + timedelta(days=late_base + round(late_per * last)))


def resolve_stage_windows(sources: list[dict], itinerary: dict) -> int:
    """Turn `stages: [a, b]` into analyze_from/stop_after, in place.

    Deliberately reuses the existing date gating instead of adding a second
    mechanism: once these fields are set, effective_analyze() and due_reason()
    handle everything and stay pure and testable. An explicitly-configured
    analyze_from or stop_after always wins, so a source can opt out.
    """
    resolved = 0
    for src in sources:
        stages = src.get("stages")
        if not stages:
            continue
        window = stage_window([int(s) for s in stages], itinerary)
        if window is None:
            continue
        start, end = window
        if not src.get("analyze_from"):
            src["analyze_from"] = start.isoformat()
        if not src.get("stop_after"):
            src["stop_after"] = end.isoformat()
        resolved += 1
    return resolved


def eid_of(item) -> str:
    return item.get("id") or item.get("link") or item.get("title", "")


def triage(candidates: list[dict], limit: int) -> tuple[list[dict], list[dict]]:
    """Split candidates into (process now, defer). Never drops anything.

    Priority, highest first:
      1. alert-tier — must never wait behind routine churn
      2. most-deferred — the anti-starvation guarantee, so a source can't be
         perpetually crowded out by higher-weight neighbours
      3. heaviest weight
      4. oldest unreported change
    """
    ordered = sorted(
        candidates,
        key=lambda c: (c["notify"] == "alert", c["deferred_count"], c["weight"],
                       c["last_change_seen"] or ""),
        reverse=True,
    )
    return ordered[:limit], ordered[limit:]


def commit_candidate(c: dict) -> None:
    """Write the ledger state that marks this candidate's change as reported."""
    entry = c["entry"]
    if c["kind"] == "html":
        digest, extractor, text = c["commit"]
        entry["last_hash"] = digest
        entry["extractor"] = extractor
        entry["extracted"] = text
    else:
        entry["seen_ids"] = retain_recent(entry.get("seen_ids", []),
                                          [c["eid"]], 300)
    entry["deferred_count"] = 0


def defer_candidate(c: dict) -> None:
    """Leave the ledger untouched so the next run re-detects this change."""
    c["entry"]["deferred_count"] = c["deferred_count"] + 1


def mark_ok(entry: dict) -> None:
    """Record a successful fetch and CLEAR any previous failure.

    Clearing matters: without it `last_error` latches forever, so one bad
    afternoon would mark a source permanently broken in the health panel — and a
    panel that cries wolf is a panel you learn to ignore.
    """
    stamp = now_iso()
    entry["last_checked"] = stamp
    entry["last_ok"] = stamp
    entry.pop("last_error", None)
    entry.pop("last_error_msg", None)


def mark_failed(entry: dict, message: str) -> None:
    """Record a failed fetch, keeping the reason for the health panel."""
    stamp = now_iso()
    entry["last_checked"] = stamp
    entry["last_error"] = stamp
    entry["last_error_msg"] = str(message)[:200]


def retain_recent(previous: list[str], fresh: list[str], cap: int) -> list[str]:
    """Append newly-seen ids and keep the newest `cap`, deterministically.

    The old implementation was `list(set(seen))[-cap:]`. Python randomises string
    hashing per process, so that retained an arbitrary subset that CHANGED every
    run — meaning an evicted id could reappear later and be reported as fresh,
    and the ledger JSON differed on every run even when nothing had changed.
    Order-preserving and de-duplicated, oldest dropped first.
    """
    out = list(previous)
    known = set(out)
    for eid in fresh:
        if eid not in known:
            out.append(eid)
            known.add(eid)
    return out[-cap:]


def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def load_ledger() -> dict:
    """state/sources.json, migrating from the older source_state.json once."""
    ledger = load_json(LEDGER_FILE, None)
    if ledger is not None:
        return ledger
    legacy = load_json(LEGACY_STATE_FILE, {})
    migrated = {}
    for key, entry in legacy.items():
        seen = entry.get("last_seen")
        migrated[key] = {
            "last_checked": seen,
            "last_hash": entry.get("hash"),
            "last_change_seen": seen,
            "extracted": entry.get("extracted", ""),
            "seen_ids": entry.get("seen_ids", []),
        }
    if migrated:
        print(f"  (migrated {len(migrated)} source(s) from source_state.json)")
    return migrated


def main() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(SOURCES, encoding="utf-8") as fh:
        sources = yaml.safe_load(fh)["sources"]
    itinerary = load_json(ITINERARY_FILE, {})
    if itinerary:
        n = resolve_stage_windows(sources, itinerary)
        if n:
            print(f"  · resolved stage windows for {n} source(s) from the itinerary")
    ledger = load_ledger()
    new_items: list[dict] = []
    candidates: list[dict] = []
    baselined = 0
    skipped = 0
    changed_quiet = 0
    rebaselined = 0
    failed: list[str] = []

    now = datetime.now(timezone.utc)
    today = now.date()
    print(f"[{now_iso()}] checking {len(sources)} sources …")

    for src in sources:
        key = sid(src["url"])
        entry = ledger.setdefault(key, {})
        notify = effective_notify(src, today)
        # stamp identity before any early-out, so the ledger always describes
        # every configured source and not just the ones checked this run
        entry["url"] = src["url"]
        entry["name"] = src["name"]
        entry["notify"] = notify

        skip = due_reason(src, entry, today, now)
        if skip:
            skipped += 1
            print(f"- {src['name']}\n  · skip: {skip}")
            continue

        tag = "!" if notify == "alert" else "·"
        print(f"- {src['name']} [{tag} {notify}]")

        meta = {
            "source_name": src["name"], "url": src["url"],
            "region": src.get("region", "whole"), "tier": src.get("tier", "other"),
            "lang": src.get("lang", "es"), "weight": float(src.get("weight", 1.0)),
            "notify": notify,
        }
        analyze = effective_analyze(src, today)

        if src["type"] == "rss":
            # Deliberately our own parser, not PyPI feedparser — see rss_compat.
            import rss_compat

            feed = rss_compat.parse(src["url"])
            if not getattr(feed, "ok", True):
                failed.append(src["name"])
                mark_failed(entry, getattr(feed, "error", "rss fetch failed"))
                continue
            # first_sight MUST be computed before mark_ok(), which stamps
            # last_checked — otherwise a brand-new feed looks already-seen and
            # dumps its whole backlog as "news" on the very first run.
            first_sight = not entry.get("seen_ids") and not entry.get("last_checked")
            mark_ok(entry)
            seen = set(entry.get("seen_ids", []))
            fresh, fresh_ids = [], []
            for item in feed.entries[:25]:
                eid = item.get("id") or item.get("link") or item.get("title", "")
                if eid and eid not in seen:
                    fresh.append(item)
                    fresh_ids.append(eid)
                    seen.add(eid)
            if first_sight:
                baselined += 1  # record what's there now, emit nothing
            elif fresh:
                entry["last_change_seen"] = now_iso()
                if analyze == "never":
                    changed_quiet += len(fresh)
                else:
                    for item in fresh:
                        body = f"{item.get('title','')}\n{item.get('summary','')}".strip()
                        extra = {}
                        pub, pub_url = item.get("source_title"), item.get("source_url")
                        if pub or pub_url:
                            extra = {"publisher": pub or "", "publisher_url": pub_url or ""}
                            # Google News <link> is an opaque JS interstitial that
                            # cannot be resolved server-side, so the publisher is
                            # the only way a reader can find the actual article.
                            body = f"{body}\n(Published by {pub or pub_url})"
                        candidates.append({
                            "item": {**meta, **extra,
                                     "url": item.get("link", src["url"]),
                                     "text": body[:MAX_CHARS]},
                            "entry": entry, "name": src["name"],
                            "weight": meta["weight"], "notify": notify,
                            "kind": "rss", "eid": eid_of(item),
                            "deferred_count": int(entry.get("deferred_count", 0)),
                            "last_change_seen": entry.get("last_change_seen", ""),
                        })
            # Ids for emitted items are committed after triage; a deferred entry
            # must stay unseen so the next run reports it again.
            if analyze == "never" or first_sight or not fresh:
                entry["seen_ids"] = retain_recent(entry.get("seen_ids", []),
                                                  fresh_ids, 300)

        else:  # html | pdf
            kind = "pdf" if src["type"] == "pdf" else "html"
            raw, err = fetch_raw(src["url"], key, kind)
            if raw is None:
                # A dead source must not be indistinguishable from a quiet one.
                failed.append(src["name"])
                mark_failed(entry, err or "fetch failed")
                continue
            mark_ok(entry)
            if kind == "pdf":
                text = extract_pdf_text(raw)
                # Prefer hashing the text layer: PDF bytes can churn on metadata
                # alone, which would fake a timetable revision every run.
                digest = hashlib.sha256(
                    text.encode() if len(text) > 40 else raw).hexdigest()
                if len(text) <= 40:
                    text = (f"(PDF at {src['url']} changed; {len(raw)} bytes, no "
                            f"usable text layer — open it to see what moved.)")
                extractor = "pdf"
            else:
                text, extractor = extract_main_text(
                    raw.decode("utf-8", "replace"), src["url"])
                if not text.strip():
                    print("  ! extraction produced nothing; leaving baseline alone")
                    continue
                # Hash the whitespace-normalised text so formatting jitter alone
                # cannot look like a content change.
                digest = hashlib.sha256(norm_for_hash(text).encode()).hexdigest()
            previous_hash = entry.get("last_hash")
            previous_text = entry.get("extracted", "")
            previous_extractor = entry.get("extractor")

            if not previous_hash:
                baselined += 1
            elif previous_extractor and previous_extractor != extractor:
                # Different extractor => different text shape => the diff would
                # be the whole page. Re-baseline instead of inventing a finding.
                print(f"  · re-extracted with {extractor} (was {previous_extractor})"
                      f"; baseline refreshed, no finding")
                rebaselined += 1
            elif not previous_text.strip():
                # A hash with no stored text: added_text() would return the whole
                # page verbatim. Adopt the text quietly instead.
                print("  · baseline had no stored text; refreshed, no finding")
                rebaselined += 1
            elif previous_hash != digest:
                delta = added_text(previous_text, text)
                entry["last_change_seen"] = now_iso()
                whole_page = len(delta) >= 0.7 * max(len(text), 1)
                if whole_page:
                    # Almost everything "changed" on a page we already had, which
                    # in practice means re-extraction churn, not news.
                    print(f"  · delta is {100 * len(delta) // max(len(text), 1)}% of "
                          f"the page — suspected churn, re-baselined, no finding")
                    rebaselined += 1
                elif delta.strip():
                    if analyze == "never":
                        changed_quiet += 1
                    else:
                        candidates.append({
                            "item": {**meta, "text": delta[:MAX_CHARS]},
                            "entry": entry, "name": src["name"],
                            "weight": meta["weight"], "notify": notify,
                            "kind": "html", "commit": (digest, extractor, text),
                            "deferred_count": int(entry.get("deferred_count", 0)),
                            "last_change_seen": entry.get("last_change_seen", ""),
                        })

            # The baseline write is DEFERRABLE. Change detection rests entirely on
            # last_hash/extracted, so withholding these three makes the next run
            # recompute the same digest and re-emit the same item. That is the whole
            # deferral mechanism — no separate queue to keep in sync.
            if not any(c.get("entry") is entry and c["kind"] == "html"
                       for c in candidates):
                entry["last_hash"] = digest
                entry["extractor"] = extractor
                entry["extracted"] = text

        time.sleep(1.0)  # courtesy pause

    # Triage: hand at most MAX_ITEMS_PER_RUN to the model and defer the rest by
    # withholding their ledger writes, so they surface again next run.
    take, defer = triage(candidates, MAX_ITEMS_PER_RUN)
    for c in take:
        commit_candidate(c)
        new_items.append(c["item"])
    for c in defer:
        defer_candidate(c)
    deferred_names = sorted({c["name"] for c in defer})
    if defer:
        print(f"  · ceiling {MAX_ITEMS_PER_RUN} reached: {len(defer)} item(s) DEFERRED "
              f"to the next run (not dropped) from: {', '.join(deferred_names)}")

    # Drop ledger entries for sources no longer configured. The ledger is keyed
    # by URL, so a retired source — or an edited URL — otherwise leaves an orphan
    # that lingers as a phantom FAILING/STALE row in the health panel forever.
    live_keys = {sid(s["url"]) for s in sources}
    orphans = [k for k in ledger if k not in live_keys]
    for k in orphans:
        ledger.pop(k)
    if orphans:
        print(f"  · pruned {len(orphans)} ledger entr(y/ies) for sources no longer "
              f"in sources.yaml")

    with open(NEW_FILE, "w", encoding="utf-8") as fh:
        json.dump(new_items, fh, ensure_ascii=False, indent=2)
    with open(LEDGER_FILE, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=2)

    alerts = sum(1 for i in new_items if i.get("notify") == "alert")
    # Persist the run summary. It used to be printed and discarded, so a run that
    # failed half its sources left no trace anywhere a human would look.
    health = load_json(HEALTH_FILE, {})
    health.update({
        "run_at": now_iso(), "sources_total": len(sources),
        "new_items": len(new_items), "alerts": alerts, "baselined": baselined,
        "rebaselined": rebaselined, "skipped": skipped,
        "changed_quiet": changed_quiet, "failed": failed,
        "deferred_items": len(defer), "deferred": deferred_names,
    })
    with open(HEALTH_FILE, "w", encoding="utf-8") as fh:
        json.dump(health, fh, ensure_ascii=False, indent=2, sort_keys=True)

    print(f"[{now_iso()}] {len(new_items)} new item(s) ({alerts} alert-tier); "
          f"{baselined} baselined; {rebaselined} re-baselined (churn suppressed); "
          f"{skipped} skipped (not due); {changed_quiet} silent change(s); "
          f"{len(defer)} deferred; {len(failed)} FAILED. Wrote {NEW_FILE}")
    if failed:
        print("  ! unreachable this run: " + ", ".join(failed))


if __name__ == "__main__":
    main()
