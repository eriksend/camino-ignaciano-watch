#!/usr/bin/env python3
"""
fetch_sources.py — the deterministic half of the watch.

Reads sources.yaml, fetches each source, extracts the meaningful body text,
diffs it against the stored baseline in state/source_state.json, and writes the
new/changed chunks to state/new_items.json. No translation or scoring happens
here — that's the routine's Claude session, which reads new_items.json next.

Run from the repo root:  python scripts/fetch_sources.py
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone

import requests
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(ROOT, "sources.yaml")
STATE_DIR = os.path.join(ROOT, "state")
CACHE_DIR = os.path.join(STATE_DIR, "cache")
STATE_FILE = os.path.join(STATE_DIR, "source_state.json")
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


def main() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(SOURCES, encoding="utf-8") as fh:
        sources = yaml.safe_load(fh)["sources"]
    state = load_json(STATE_FILE, {})
    new_items: list[dict] = []
    baselined = 0

    print(f"[{now_iso()}] checking {len(sources)} sources …")
    for src in sources:
        key = sid(src["url"])
        meta = {
            "source_name": src["name"], "url": src["url"],
            "region": src.get("region", "whole"), "tier": src.get("tier", "other"),
            "lang": src.get("lang", "es"), "weight": float(src.get("weight", 1.0)),
        }
        print(f"- {src['name']}")

        if src["type"] == "rss":
            import feedparser

            feed = feedparser.parse(src["url"])
            first_sight = key not in state
            seen = set(state.get(key, {}).get("seen_ids", []))
            fresh = []
            for entry in feed.entries[:25]:
                eid = entry.get("id") or entry.get("link") or entry.get("title", "")
                if eid and eid not in seen:
                    fresh.append(entry)
                    seen.add(eid)
            if first_sight:
                baselined += 1  # record what's there now, emit nothing
            else:
                for entry in fresh:
                    body = f"{entry.get('title','')}\n{entry.get('summary','')}".strip()
                    new_items.append({**meta, "url": entry.get("link", src["url"]),
                                      "text": body[:MAX_CHARS]})
            state[key] = {"seen_ids": list(seen)[-300:], "last_seen": now_iso()}

        else:  # html
            html = fetch(src["url"], key)
            if html is None:
                continue
            text = extract_main_text(html, src["url"])
            h = hashlib.sha256(text.encode()).hexdigest()
            prev = state.get(key)
            _, prev_path = cache_paths(key)
            if not prev:
                with open(prev_path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                state[key] = {"hash": h, "extracted": text, "last_seen": now_iso()}
                baselined += 1
            elif prev.get("hash") != h:
                # preserve previous extracted text before overwriting
                with open(prev_path, "w", encoding="utf-8") as fh:
                    fh.write(prev.get("extracted", ""))
                delta = added_text(prev.get("extracted", ""), text)
                state[key] = {"hash": h, "extracted": text, "last_seen": now_iso()}
                if delta.strip():
                    new_items.append({**meta, "text": delta[:MAX_CHARS]})
            else:
                state[key]["last_seen"] = now_iso()
        time.sleep(1.0)  # courtesy pause

    with open(NEW_FILE, "w", encoding="utf-8") as fh:
        json.dump(new_items, fh, ensure_ascii=False, indent=2)
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)

    print(f"[{now_iso()}] {len(new_items)} new item(s); {baselined} source(s) "
          f"baselined this run. Wrote {NEW_FILE}")


if __name__ == "__main__":
    main()
