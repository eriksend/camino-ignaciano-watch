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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es,en;q=0.9,eu;q=0.8,ca;q=0.7",
}
MAX_CHARS = 6000  # cap per chunk handed to the model

# Cadence -> minimum hours between checks. Deliberately under the nominal
# interval so a run firing slightly early doesn't skip a source.
CADENCE_HOURS = {"twice_daily": 10, "daily": 20, "weekly": 156}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sid(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def cache_paths(key: str) -> tuple[str, str]:
    """Return (latest_html_path, prev_text_path) for a source key."""
    return (
        os.path.join(CACHE_DIR, f"{key}-latest.html"),
        os.path.join(CACHE_DIR, f"{key}-prev.txt"),
    )


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


def fetch(url: str, key: str) -> str | None:
    """Fetch URL; on success write raw HTML to cache. Falls back to cached copy."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    html_path, _ = cache_paths(key)
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        with open(html_path, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(r.text)
        return r.text
    except Exception as exc:
        if os.path.exists(html_path):
            print(f"  ! fetch failed ({exc}); using cached copy")
            with open(html_path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        print(f"  ! fetch failed: {exc}")
        return None


def extract_main_text(html: str, url: str) -> str:
    """Main body text, boilerplate removed, so we diff on content not chrome."""
    try:
        import trafilatura

        text = trafilatura.extract(html, url=url, favor_recall=True,
                                   include_comments=True)
        if text and len(text.strip()) > 40:
            return text.strip()
    except Exception:
        pass
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        return " ".join(soup.get_text(" ").split())
    except Exception:
        return ""


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
    ledger = load_ledger()
    new_items: list[dict] = []
    baselined = 0
    skipped = 0
    changed_quiet = 0

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
        analyze = str(src.get("analyze", "on_change")).strip()

        if src["type"] == "rss":
            import feedparser

            feed = feedparser.parse(src["url"])
            first_sight = not entry.get("seen_ids") and not entry.get("last_checked")
            seen = set(entry.get("seen_ids", []))
            fresh = []
            for item in feed.entries[:25]:
                eid = item.get("id") or item.get("link") or item.get("title", "")
                if eid and eid not in seen:
                    fresh.append(item)
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
                        new_items.append({**meta, "url": item.get("link", src["url"]),
                                          "text": body[:MAX_CHARS]})
            entry["seen_ids"] = list(seen)[-300:]
            entry["last_checked"] = now_iso()

        else:  # html
            html = fetch(src["url"], key)
            if html is None:
                continue
            text = extract_main_text(html, src["url"])
            digest = hashlib.sha256(text.encode()).hexdigest()
            previous_hash = entry.get("last_hash")
            _, prev_path = cache_paths(key)

            if not previous_hash:
                with open(prev_path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                baselined += 1
            elif previous_hash != digest:
                # preserve previous extracted text before overwriting
                with open(prev_path, "w", encoding="utf-8") as fh:
                    fh.write(entry.get("extracted", ""))
                delta = added_text(entry.get("extracted", ""), text)
                entry["last_change_seen"] = now_iso()
                if delta.strip():
                    if analyze == "never":
                        changed_quiet += 1
                    else:
                        new_items.append({**meta, "text": delta[:MAX_CHARS]})

            entry["last_hash"] = digest
            entry["extracted"] = text
            entry["last_checked"] = now_iso()

        time.sleep(1.0)  # courtesy pause

    with open(NEW_FILE, "w", encoding="utf-8") as fh:
        json.dump(new_items, fh, ensure_ascii=False, indent=2)
    with open(LEDGER_FILE, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=2)

    alerts = sum(1 for i in new_items if i.get("notify") == "alert")
    print(f"[{now_iso()}] {len(new_items)} new item(s) ({alerts} alert-tier); "
          f"{baselined} baselined; {skipped} skipped (not due); "
          f"{changed_quiet} silent change(s). Wrote {NEW_FILE}")


if __name__ == "__main__":
    main()
