#!/usr/bin/env python3
"""
fetch_fires.py — wildfire detection geofenced to the Camino Ignaciano corridor.

Two jobs, deliberately scored differently (see SKILL.md):

  AFTERMATH (default, any date outside the walk window)
      Did fire burn on or near the route? Damage to waymarking, tracks, shade,
      bridges and pilgrim lodging persists for months. notify: quiet.

  LIVE RISK (inside the walk window 2027-04-19 .. 2027-05-20)
      Active fire near a stage the walker is about to reach. notify: alert.

Detections come from NASA FIRMS. A map key is required and free:
    request:  https://firms.modaps.eosdis.nasa.gov/api/map_key/
    api docs: https://firms.modaps.eosdis.nasa.gov/api/area/
Set it as FIRMS_MAP_KEY in the environment. Without it this script exits 0
having written an empty result, so the daily routine never breaks.

Geofence: one bounding box over the whole corridor, then a haversine filter to
the 27 stage endpoints in data/end_coords.json. Anything within 20 km is
reported with the stage(s) it is near.

Usage:
    python scripts/fetch_fires.py                 # live/aftermath sweep, last 7 days
    python scripts/fetch_fires.py --days 3
    python scripts/fetch_fires.py --check         # is the FIRMS API serving?
    python scripts/fetch_fires.py --retro 2025 2026   # historical Jun-Sep sweep
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COORDS_FILE = os.path.join(ROOT, "data", "end_coords.json")
STATE_DIR = os.path.join(ROOT, "state")
OUT_FILE = os.path.join(STATE_DIR, "fire_items.json")

FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
LIVE_SOURCES = ["VIIRS_NOAA20_NRT", "VIIRS_SNPP_NRT"]
# NRT products only cover roughly the last two months; historical queries need
# the standard-processing archive. Retro tries SP first and falls back to NRT.
RETRO_SOURCES = ["VIIRS_SNPP_SP", "VIIRS_NOAA20_SP"]

NEAR_KM = 20.0          # flag detections within this distance of an endpoint
MAX_DAYS_PER_CALL = 10  # FIRMS area API ceiling
WALK_START = date(2027, 4, 19)
WALK_END = date(2027, 5, 20)

# The Basque/Cantabrian north has a distinct late-winter/spring fire season
# (dry fohn winds + agricultural burning) that overlaps the walk window.
SPRING_RISK_STAGES = range(1, 7)
SPRING_RISK_MONTHS = (2, 3, 4, 5)


def load_stages() -> list[dict]:
    with open(COORDS_FILE, encoding="utf-8") as fh:
        return json.load(fh)["stages"]


def load_bbox() -> dict:
    with open(COORDS_FILE, encoding="utf-8") as fh:
        return json.load(fh)["_bbox"]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def firms_url(key: str, source: str, bbox: dict, days: int,
              start: date | None = None) -> str:
    area = f"{bbox['west']},{bbox['south']},{bbox['east']},{bbox['north']}"
    url = f"{FIRMS_BASE}/{key}/{source}/{area}/{days}"
    if start:
        url += f"/{start.isoformat()}"
    return url


def fetch_csv(url: str, label: str) -> list[dict] | None:
    """Return parsed rows, or None if the request failed."""
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
    except Exception as exc:
        print(f"  ! {label}: request failed: {exc}")
        return None
    body = r.text.strip()
    if not body:
        return []
    # FIRMS returns plain-text errors (invalid key, bad product) with HTTP 200.
    if not body.lower().startswith("latitude"):
        first = body.splitlines()[0][:160]
        print(f"  ! {label}: unexpected response: {first}")
        return None
    return list(csv.DictReader(io.StringIO(body)))


def annotate(rows: list[dict], stages: list[dict]) -> list[dict]:
    """Keep detections within NEAR_KM of an endpoint, tagged with that stage."""
    hits = []
    for row in rows:
        try:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        near = []
        for stage in stages:
            d = haversine_km(lat, lon, stage["lat"], stage["lon"])
            if d <= NEAR_KM:
                near.append((d, stage))
        if not near:
            continue
        near.sort(key=lambda x: x[0])
        distance, stage = near[0]
        hits.append({
            "lat": lat, "lon": lon,
            "acq_date": row.get("acq_date", ""),
            "acq_time": row.get("acq_time", ""),
            "confidence": row.get("confidence", ""),
            "frp": row.get("frp", ""),
            "satellite": row.get("satellite", ""),
            "stage": stage["stage"],
            "stage_end": stage["end"],
            "region": stage["region"],
            "distance_km": round(distance, 1),
            "also_near": [s["end"] for _, s in near[1:4]],
        })
    return hits


def cluster(hits: list[dict]) -> list[dict]:
    """Group detections by stage and contiguous date-run, so one fire is one
    finding rather than hundreds of hotspot rows."""
    by_stage: dict[int, list[dict]] = {}
    for h in hits:
        by_stage.setdefault(h["stage"], []).append(h)

    clusters = []
    for stage, group in by_stage.items():
        group.sort(key=lambda h: h["acq_date"])
        run: list[dict] = []

        def flush(run: list[dict]) -> None:
            if not run:
                return
            dates = sorted({h["acq_date"] for h in run if h["acq_date"]})
            nearest = min(run, key=lambda h: h["distance_km"])
            frps = [float(h["frp"]) for h in run
                    if str(h.get("frp", "")).replace(".", "", 1).isdigit()]
            clusters.append({
                "stage": stage,
                "stage_end": run[0]["stage_end"],
                "region": run[0]["region"],
                "detections": len(run),
                "date_from": dates[0] if dates else "",
                "date_to": dates[-1] if dates else "",
                "min_distance_km": nearest["distance_km"],
                "centroid": [round(sum(h["lat"] for h in run) / len(run), 4),
                             round(sum(h["lon"] for h in run) / len(run), 4)],
                "max_frp": round(max(frps), 1) if frps else None,
                "also_near": nearest["also_near"],
            })

        for h in group:
            if not run:
                run = [h]
                continue
            try:
                gap = (date.fromisoformat(h["acq_date"])
                       - date.fromisoformat(run[-1]["acq_date"])).days
            except ValueError:
                gap = 0
            if gap <= 2:
                run.append(h)
            else:
                flush(run)
                run = [h]
        flush(run)

    clusters.sort(key=lambda c: (c["min_distance_km"], c["date_from"]))
    return clusters


def to_items(clusters: list[dict], mode: str, today: date) -> list[dict]:
    """Shape clusters into new_items-style records for the routine to score."""
    live = WALK_START <= today <= WALK_END
    items = []
    for c in clusters:
        span = c["date_from"] if c["date_from"] == c["date_to"] \
            else f"{c['date_from']} to {c['date_to']}"
        spring = (c["stage"] in SPRING_RISK_STAGES
                  and today.month in SPRING_RISK_MONTHS)
        notify = "alert" if (live or (spring and mode == "live")) else "quiet"
        also = f" Also within {int(NEAR_KM)} km of {', '.join(c['also_near'])}." \
            if c["also_near"] else ""
        frp = f" Peak fire radiative power {c['max_frp']} MW." if c["max_frp"] else ""
        items.append({
            "source_name": "NASA FIRMS (VIIRS active fire)",
            "url": "https://firms.modaps.eosdis.nasa.gov/map/",
            "region": c["region"],
            "tier": "official",
            "lang": "en",
            "weight": 1.3 if notify == "alert" else 1.0,
            "notify": notify,
            "kind": "fire_aftermath" if mode == "retro" or not live else "fire_live",
            "text": (
                f"VIIRS satellite recorded {c['detections']} active-fire detection(s) "
                f"{span} centred {c['centroid'][0]}, {c['centroid'][1]}, "
                f"{c['min_distance_km']} km from the stage {c['stage']} endpoint "
                f"{c['stage_end']} ({c['region']}).{also}{frp} "
                f"{'Walk is underway — treat as live hazard and check access restrictions.' if live else 'Outside the walk window — assess as route damage (waymarking, tracks, shade, bridges, lodging) rather than live hazard.'}"
            ),
        })
    return items


def sweep(key: str, sources: list[str], bbox: dict, stages: list[dict],
          days: int, start: date | None, label: str) -> list[dict]:
    hits: list[dict] = []
    for source in sources:
        url = firms_url(key, source, bbox, days, start)
        rows = fetch_csv(url, f"{label} {source}")
        if rows is None:
            continue
        found = annotate(rows, stages)
        print(f"  {label} {source}: {len(rows)} detection(s) in box, "
              f"{len(found)} within {int(NEAR_KM)} km of a stage")
        hits.extend(found)
    return hits


def retro_sweep(key: str, years: list[int], bbox: dict,
                stages: list[dict]) -> list[dict]:
    """June-September historical sweep, in 10-day chunks (API ceiling)."""
    hits: list[dict] = []
    sources = RETRO_SOURCES
    probe_done = False
    for year in years:
        cursor = date(year, 6, 1)
        end = date(year, 9, 30)
        if cursor > date.today():
            print(f"  (skipping {year}: in the future)")
            continue
        end = min(end, date.today())
        while cursor <= end:
            days = min(MAX_DAYS_PER_CALL, (end - cursor).days + 1)
            found = sweep(key, sources, bbox, stages, days, cursor,
                          f"retro {cursor.isoformat()}")
            if not probe_done:
                probe_done = True
                if not found and not sweep(key, [RETRO_SOURCES[0]], bbox, stages,
                                           days, cursor, "probe"):
                    # SP archive may be unavailable; try NRT for the rest.
                    print("  (SP archive returned nothing; falling back to NRT)")
                    sources = LIVE_SOURCES
            hits.extend(found)
            cursor += timedelta(days=days)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7,
                    help="lookback window for the live sweep (max 10)")
    ap.add_argument("--retro", nargs="*", type=int, metavar="YEAR",
                    help="historical Jun-Sep sweep for the given years")
    ap.add_argument("--check", action="store_true",
                    help="probe whether the FIRMS API is serving, then exit")
    args = ap.parse_args()

    os.makedirs(STATE_DIR, exist_ok=True)
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    stages = load_stages()
    bbox = load_bbox()
    today = datetime.now(timezone.utc).date()

    if not key:
        print("FIRMS_MAP_KEY is not set — skipping satellite fire detection.")
        print("Request a free key at https://firms.modaps.eosdis.nasa.gov/api/map_key/")
        with open(OUT_FILE, "w", encoding="utf-8") as fh:
            json.dump([], fh)
        return 0

    if args.check:
        rows = fetch_csv(firms_url(key, LIVE_SOURCES[0], bbox, 1), "check")
        if rows is None:
            print("FIRMS: NOT serving (or key rejected).")
            return 1
        print(f"FIRMS: serving. {len(rows)} detection(s) in the corridor box "
              f"in the last day.")
        return 0

    if args.retro is not None:
        years = args.retro or [2025, 2026]
        print(f"[{today}] retrospective fire sweep for {years} "
              f"over the route corridor …")
        hits = retro_sweep(key, years, bbox, stages)
        mode = "retro"
    else:
        days = max(1, min(args.days, MAX_DAYS_PER_CALL))
        print(f"[{today}] fire sweep, last {days} day(s) over the route corridor …")
        hits = sweep(key, LIVE_SOURCES, bbox, stages, days, None, "live")
        mode = "live"

    clusters = cluster(hits)
    items = to_items(clusters, mode, today)
    with open(OUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=2)

    alerts = sum(1 for i in items if i["notify"] == "alert")
    print(f"{len(hits)} detection(s) near the route -> {len(clusters)} cluster(s), "
          f"{len(items)} item(s) ({alerts} alert-tier). Wrote {OUT_FILE}")
    for c in clusters[:10]:
        print(f"  · stage {c['stage']} {c['stage_end']}: {c['detections']} det, "
              f"{c['min_distance_km']} km, {c['date_from']}..{c['date_to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
