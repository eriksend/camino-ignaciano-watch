"""Minimal RSS/Atom parser over stdlib xml.etree.ElementTree.

WHY THIS EXISTS, AND WHY IT IS NOT feedparser.

The PyPI `feedparser` depends on `sgmllib3k`, whose wheel build fails on some
Python/toolchain combinations — it failed in this project's own environment. For a
job that runs unattended every day for months and must not break at 14:42 UTC on a
morning nobody is watching, a 99-line parser you can read in full beats a
dependency whose install has already failed once.

It used to live at `scripts/feedparser.py`, which SHADOWED the real package:
`scripts/` is on sys.path when these scripts run, so `import feedparser` silently
resolved here instead of to the installed library. That worked but was invisible.
Now it has its own name and is imported deliberately.

Provides just enough of the feedparser API for fetch_sources.py:
  - parse(url) -> namespace with .entries list
  - each entry: dict-like with 'id', 'link', 'title', 'summary'
"""
from __future__ import annotations
import requests
import xml.etree.ElementTree as ET

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; camino-watch/1.0)",
    "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
}

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media": "http://search.yahoo.com/mrss/",
}


class _Entry(dict):
    """Dict subclass so .get() works and attribute access works too."""
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            return ""


class _Feed:
    def __init__(self, entries, ok=True, error=None):
        self.entries = entries
        self.feed = {}
        self.bozo = not ok
        # `ok` exists so a FAILED fetch is distinguishable from an EMPTY feed.
        # Without it a dead feed looks exactly like a quiet one, which is the
        # defining failure mode for a monitor.
        self.ok = ok
        self.error = error


def _text(el, *tags):
    for tag in tags:
        child = el.find(tag, _NS)
        if child is not None and child.text:
            return child.text.strip()
    return ""


def _parse_rss(root):
    entries = []
    channel = root.find("channel")
    items = (channel or root).findall("item")
    for item in items:
        entry = _Entry()
        entry["title"] = _text(item, "title")
        entry["link"] = _text(item, "link")
        entry["id"] = _text(item, "guid") or entry["link"] or entry["title"]
        entry["summary"] = _text(item, "description", "content:encoded")
        # Aggregators (notably Google News) put an opaque redirect in <link> and
        # name the real publisher here. It is the only usable provenance they give.
        src_el = item.find("source")
        if src_el is not None:
            entry["source_title"] = (src_el.text or "").strip()
            entry["source_url"] = src_el.get("url", "")
        entries.append(entry)
    return entries


def _parse_atom(root):
    entries = []
    for item in root.findall("{http://www.w3.org/2005/Atom}entry"):
        entry = _Entry()
        entry["title"] = _text(item, "atom:title")
        link_el = item.find("{http://www.w3.org/2005/Atom}link[@rel='alternate']")
        if link_el is None:
            link_el = item.find("{http://www.w3.org/2005/Atom}link")
        entry["link"] = link_el.get("href", "") if link_el is not None else ""
        entry["id"] = _text(item, "atom:id") or entry["link"] or entry["title"]
        entry["summary"] = _text(item, "atom:summary", "atom:content")
        entries.append(entry)
    return entries


def parse(url_or_file):
    url = url_or_file
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        content = resp.content
    except Exception as e:
        print(f"  ! rss fetch failed: {e}")
        return _Feed([], ok=False, error=f"fetch: {e}")

    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        print(f"  ! rss XML parse error: {e}")
        return _Feed([], ok=False, error=f"xml: {e}")

    tag = root.tag.lower() if root.tag else ""
    if "atom" in tag or root.tag == "{http://www.w3.org/2005/Atom}feed":
        entries = _parse_atom(root)
    else:
        entries = _parse_rss(root)

    return _Feed(entries)
