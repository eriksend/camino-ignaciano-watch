#!/usr/bin/env python3
"""
preflight.py — fast, offline sanity check the routine runs BEFORE doing any work.

Why this exists rather than a CI pipeline: the failure this project actually
suffers from is a silent one. A broken `sources.yaml`, a renamed region, a
corrupted ledger or a syntax error in a script would let the run proceed and
produce nothing, which looks exactly like "no news today". This turns that into a
loud, immediate failure with a reason attached.

It uses only the standard library plus PyYAML, both already required, so it needs
no extra install and makes no network calls. Unit tests live in tests/ and are a
developer tool; THIS is the check that guards the unattended daily run.

Exit codes:
    0  fine (warnings may still be printed)
    1  at least one hard failure — the routine should STOP and report, not continue

Usage:  python scripts/preflight.py
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(ROOT, "sources.yaml")
DATA = os.path.join(ROOT, "data")
STATE = os.path.join(ROOT, "state")

VALID_TYPES = {"html", "rss", "pdf"}
VALID_TIERS = {"official", "guide", "forum", "blog", "town", "tour", "social",
               "discovered"}
VALID_NOTIFY = {"quiet", "alert"}
MAX_FINDINGS = 500
MAX_SOURCE_TEXT = 6000
ALERT_READY_DAYS = 60   # an alert source silent later than this is a trap
# Degrees corresponding to the 20 km geofence radius in fetch_fires.NEAR_KM,
# at this corridor's latitude. Used to check the bbox clears every endpoint.
NEAR_KM_DEG_LAT = 0.18
NEAR_KM_DEG_LON = 0.25

errors: list[str] = []
warnings: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def load_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def check_imports() -> dict:
    """Every script must import. Catches a syntax error before the run commits."""
    mods = {}
    for name in ("walk_window", "condition_ledger", "rss_compat", "fetch_sources",
                 "fetch_fires", "fetch_alfa", "build_report"):
        try:
            mods[name] = importlib.import_module(name)
        except Exception as exc:
            fail(f"scripts/{name}.py does not import: {type(exc).__name__}: {exc}")
    return mods


def check_sources(mods: dict) -> None:
    try:
        import yaml
        with open(SOURCES, encoding="utf-8") as fh:
            srcs = yaml.safe_load(fh)["sources"]
    except Exception as exc:
        fail(f"sources.yaml does not parse: {exc}")
        return
    if not srcs:
        fail("sources.yaml has no sources")
        return

    fs = mods.get("fetch_sources")
    br = mods.get("build_report")
    ww = mods.get("walk_window")
    as_date = getattr(fs, "as_date", None)
    regions = set(getattr(br, "REGION_COLORS", {}))
    seen_urls: dict[str, str] = {}

    for i, s in enumerate(srcs):
        label = s.get("name") or f"entry #{i}"
        for req in ("name", "url", "type"):
            if not s.get(req):
                fail(f"{label}: missing required field '{req}'")
        if s.get("type") not in VALID_TYPES:
            fail(f"{label}: type {s.get('type')!r} not in {sorted(VALID_TYPES)}")
        if s.get("url") in seen_urls:
            fail(f"{label}: duplicate url, already used by {seen_urls[s['url']]}")
        elif s.get("url"):
            seen_urls[s["url"]] = label

        try:
            w = float(s.get("weight", 1.0))
            if not 0.0 <= w <= 2.0:
                fail(f"{label}: weight {w} outside 0-2")
        except (TypeError, ValueError):
            fail(f"{label}: weight {s.get('weight')!r} is not a number")

        # A typo'd region renders grey AND silently drops out of the filter buttons.
        if regions and s.get("region") and s["region"] not in regions:
            fail(f"{label}: region {s['region']!r} unknown to build_report "
                 f"(valid: {sorted(regions)})")
        if s.get("tier") and s["tier"] not in VALID_TIERS:
            warn(f"{label}: tier {s['tier']!r} not in the documented set")
        if s.get("notify") and s["notify"] not in VALID_NOTIFY:
            fail(f"{label}: notify {s['notify']!r} not in {sorted(VALID_NOTIFY)}")

        dates = {}
        for field in ("notify_from", "analyze_from", "dormant_until", "stop_after"):
            if s.get(field) is None:
                continue
            d = as_date(s[field]) if as_date else None
            if d is None:
                fail(f"{label}: {field}={s[field]!r} is not a usable date")
            else:
                dates[field] = d

        stop = dates.get("stop_after")
        for field in ("notify_from", "analyze_from", "dormant_until"):
            if stop and dates.get(field) and dates[field] > stop:
                warn(f"{label}: {field} ({dates[field]}) is after stop_after "
                     f"({stop}) — this source stops before it ever speaks")

        # The trap that once had 9 alert sources first executing mid-walk.
        if ww:
            ready_by = ww.WALK_START - dt.timedelta(days=ALERT_READY_DAYS)
            silent_until = dates.get("analyze_from") or dates.get("dormant_until")
            is_alert = s.get("notify") == "alert" or "notify_from" in dates
            if is_alert and silent_until and silent_until > ready_by:
                warn(f"{label}: alert-tier but silent until {silent_until}, "
                     f"less than {ALERT_READY_DAYS} days before the walk — "
                     f"its behaviour will be unproven when it matters")

    print(f"  sources.yaml: {len(srcs)} sources, {len(seen_urls)} distinct URLs")


def check_data(mods: dict) -> None:
    try:
        coords = load_json(os.path.join(DATA, "end_coords.json"))
    except Exception as exc:
        fail(f"data/end_coords.json unreadable: {exc}")
        return
    stages = coords.get("stages", [])
    bbox = coords.get("_bbox", {})
    nums = [s.get("stage") for s in stages]
    if len(stages) != 27:
        fail(f"end_coords.json has {len(stages)} stages, expected 27")
    if len(set(nums)) != len(nums):
        fail("end_coords.json has duplicate stage numbers")
    # The box must clear every endpoint by the geofence radius, not merely
    # contain it: FIRMS is queried with this box, so anything in an uncovered
    # strip is invisible rather than merely mis-sampled. The original box failed
    # this on all four sides and excluded two endpoints outright.
    margin_lat = NEAR_KM_DEG_LAT
    margin_lon = NEAR_KM_DEG_LON
    for s in stages:
        lat, lon = s.get("lat"), s.get("lon")
        if lat is None or lon is None:
            fail(f"stage {s.get('stage')} missing coordinates")
            continue
        if not (bbox.get("south", -90) <= lat <= bbox.get("north", 90)
                and bbox.get("west", -180) <= lon <= bbox.get("east", 180)):
            fail(f"stage {s.get('stage')} ({s.get('end')}) at {lat},{lon} "
                 f"is OUTSIDE the corridor bbox — fire detection there is blind")
            continue
        short = []
        if lat - bbox["south"] < margin_lat:
            short.append(f"south by {margin_lat - (lat - bbox['south']):.3f}deg")
        if bbox["north"] - lat < margin_lat:
            short.append(f"north by {margin_lat - (bbox['north'] - lat):.3f}deg")
        if lon - bbox["west"] < margin_lon:
            short.append(f"west by {margin_lon - (lon - bbox['west']):.3f}deg")
        if bbox["east"] - lon < margin_lon:
            short.append(f"east by {margin_lon - (bbox['east'] - lon):.3f}deg")
        if short:
            fail(f"stage {s.get('stage')} ({s.get('end')}) is inside the bbox but "
                 f"the box does not clear it by the 20 km geofence radius: "
                 f"{', '.join(short)}")

    try:
        bands = load_json(os.path.join(DATA, "climate_bands.json"))
        mapped: list[int] = []
        for band in bands.get("bands", {}).values():
            mapped.extend(band.get("stages", []))
        missing = sorted(set(nums) - set(mapped))
        dupes = sorted({n for n in mapped if mapped.count(n) > 1})
        if missing:
            fail(f"climate_bands.json does not map stage(s) {missing}")
        if dupes:
            fail(f"climate_bands.json maps stage(s) {dupes} to more than one band")
        print(f"  data: 27 stages, all inside bbox, all mapped to a climate band")
    except Exception as exc:
        fail(f"data/climate_bands.json unreadable: {exc}")


def check_findings() -> None:
    path = os.path.join(STATE, "findings.json")
    if not os.path.exists(path):
        print("  findings.json: absent (first run)")
        return
    try:
        findings = load_json(path)
    except Exception as exc:
        fail(f"state/findings.json is not valid JSON: {exc}")
        return
    ids = [f.get("id") for f in findings]
    if len(set(ids)) != len(ids):
        fail(f"findings.json has duplicate ids "
             f"({len(ids) - len(set(ids))} duplicate(s))")
    if len(findings) > MAX_FINDINGS:
        warn(f"findings.json holds {len(findings)}, above the {MAX_FINDINGS} cap")
    for f in findings:
        fid = f.get("id", "?")
        r = f.get("relevance")
        if not isinstance(r, int) or not 0 <= r <= 100:
            fail(f"finding {fid}: relevance {r!r} is not an int 0-100")
        if f.get("notify") is not None and f["notify"] not in VALID_NOTIFY:
            fail(f"finding {fid}: notify {f['notify']!r} invalid")
        if f.get("source_text") and len(f["source_text"]) > MAX_SOURCE_TEXT:
            warn(f"finding {fid}: source_text is {len(f['source_text'])} chars, "
                 f"over the {MAX_SOURCE_TEXT} cap")
    print(f"  findings.json: {len(findings)} findings, ids unique")


def check_state(mods: dict) -> None:
    for name in ("sources.json", "conditions.json", "health.json"):
        path = os.path.join(STATE, name)
        if not os.path.exists(path):
            print(f"  {name}: absent (written on the next run)")
            continue
        try:
            data = load_json(path)
            print(f"  {name}: loads, {len(data)} entr{'y' if len(data) == 1 else 'ies'}")
        except Exception as exc:
            fail(f"state/{name} is not valid JSON: {exc}")

    ww = mods.get("walk_window")
    if ww and not ww.WALK_START < ww.WALK_END:
        fail(f"walk_window: WALK_START {ww.WALK_START} is not before "
             f"WALK_END {ww.WALK_END}")


def main() -> int:
    print("preflight: checking config and state (offline) …")
    mods = check_imports()
    if mods:
        print(f"  imports: {len(mods)}/7 scripts load")
    check_sources(mods)
    check_data(mods)
    check_findings()
    check_state(mods)

    if not os.environ.get("FIRMS_MAP_KEY", "").strip():
        warn("FIRMS_MAP_KEY unset — satellite fire detection is off "
             "(the EFFIS Fire Weather Index still runs; this is expected)")

    for w in warnings:
        print(f"  WARN  {w}")
    for e in errors:
        print(f"  FAIL  {e}")
    if errors:
        print(f"preflight FAILED: {len(errors)} problem(s), {len(warnings)} warning(s). "
              f"Stop and report — do not continue the run.")
        return 1
    print(f"preflight OK ({len(warnings)} warning(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
