#!/usr/bin/env python3
"""
fetch_fires.py — wildfire detection geofenced to the Camino Ignaciano corridor.

Two jobs, deliberately scored differently (see SKILL.md):

  AFTERMATH (default, any date outside the walk window)
      Did fire burn on or near the route? Damage to waymarking, tracks, shade,
      bridges and pilgrim lodging persists for months. notify: quiet.

  LIVE RISK (inside the walk window 2027-04-20 .. 2027-05-27)
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
import struct
import os
import sys
from datetime import date, datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COORDS_FILE = os.path.join(ROOT, "data", "end_coords.json")
STATE_DIR = os.path.join(ROOT, "state")
HEALTH_FILE = os.path.join(STATE_DIR, "health.json")
OUT_FILE = os.path.join(STATE_DIR, "fire_items.json")

FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
LIVE_SOURCES = ["VIIRS_NOAA20_NRT", "VIIRS_SNPP_NRT"]
# NRT products only cover roughly the last two months; historical queries need
# the standard-processing archive. Retro tries SP first and falls back to NRT.
RETRO_SOURCES = ["VIIRS_SNPP_SP", "VIIRS_NOAA20_SP"]

NEAR_KM = 20.0          # flag detections within this distance of an endpoint
MAX_DAYS_PER_CALL = 10  # FIRMS area API ceiling
# Walk dates are shared with fetch_alfa.py; edit them in walk_window.py only.
import condition_ledger as cl  # noqa: E402
from walk_window import WALK_END, WALK_START  # noqa: E402

# ---- EFFIS Fire Weather Index (leading indicator; no API key required) ----
# The FWI *forecast* is the earliest warning available: it predicts the drying
# that ripens cereal early and pulls harvest machinery into the walk window.
# Note the published layer name "ecmwf007.fwi" in EFFIS's own docs is stale; the
# live capabilities advertise mf010.* (MeteoFrance model, ~10 km, 3-day forecast).
EFFIS_WMS = "https://maps.effis.emergency.copernicus.eu/effis"
FWI_LAYER = "mf010.fwi"
FWI_ANOMALY_LAYER = "mf010.anomaly"
FWI_FORECAST_DAYS = 3
# Official EFFIS classes. "Very extreme" was added in June 2021 for the
# Mediterranean. Ordered high-to-low so the first match wins.
FWI_CLASSES = [
    (70.0, "very extreme"), (50.0, "extreme"), (38.0, "very high"),
    (21.3, "high"), (11.2, "moderate"), (0.0, "low"),
]
FWI_REPORT_FROM = 21.3   # don't emit anything below "high" — it's noise
FWI_ALERT_FROM = 38.0    # "very high" and up escalates inside the risk season
FWI_SANE_MAX = 150.0     # above this it is a nodata sentinel, not weather
FWI_HYSTERESIS = 2.0     # margin required to call a class transition real
# Set by fwi_sweep so main() can report EFFIS liveness into health.json.
FWI_REACHABLE: dict = {"ok": False}

# The Basque/Cantabrian north has a distinct late-winter/spring fire season
# (dry fohn winds + agricultural burning) that overlaps the walk window.
SPRING_RISK_STAGES = range(1, 7)
SPRING_RISK_MONTHS = (2, 3, 4, 5)
# Cereal-machinery ignition season for the Ebro/Monegros/Catalan stages. Distinct
# from SPRING_RISK_MONTHS above, which is the Basque föhn-wind season — these two
# used to be an inline tuple and a constant that silently disagreed.
CEREAL_RISK_MONTHS = (4, 5, 6)


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


def fwi_class(value: float) -> str:
    for threshold, name in FWI_CLASSES:
        if value >= threshold:
            return name
    return "low"


def _tiff_floats(data: bytes) -> tuple[list[float], int, int]:
    """Read an uncompressed single-band float32 GeoTIFF. MapServer returns a
    plain strip, so this needs no GDAL."""
    endian = "<" if data[:2] == b"II" else ">"
    off = struct.unpack(endian + "I", data[4:8])[0]
    count = struct.unpack(endian + "H", data[off:off + 2])[0]
    sizes = {1: 1, 2: 1, 3: 2, 4: 4}
    codes = {1: "B", 2: "c", 3: "H", 4: "I"}
    tags: dict[int, list[int]] = {}
    for i in range(count):
        p = off + 2 + i * 12
        tag, typ, n = struct.unpack(endian + "HHI", data[p:p + 8])
        if typ not in sizes:
            continue
        total = sizes[typ] * n
        if total <= 4:                       # value stored inline
            raw = data[p + 8:p + 8 + total]
        else:                                # value field is an offset to an array
            ptr = struct.unpack(endian + "I", data[p + 8:p + 12])[0]
            raw = data[ptr:ptr + total]
        tags[tag] = list(struct.unpack(endian + f"{n}{codes[typ]}", raw))

    def one(tag: int, default=None):
        v = tags.get(tag)
        return v[0] if v else default

    width, height = one(256, 0), one(257, 0)
    if one(339) != 3 or one(258) != 32:
        raise ValueError("unexpected FWI raster encoding "
                         f"(sampleformat={one(339)}, bits={one(258)})")
    offsets, counts = tags.get(273), tags.get(279)
    if not offsets or not counts:
        raise ValueError("FWI raster has no strips")
    # Large rasters come back as multiple strips; concatenate them in order.
    payload = b"".join(data[o:o + c] for o, c in zip(offsets, counts))
    values = list(struct.unpack(endian + f"{len(payload) // 4}f", payload))
    return values, width, height


def fetch_fwi_grid(bbox: dict, day: date, layer: str,
                   px_per_deg: int = 40) -> tuple[list[float], int, int] | None:
    """One raster covering the whole corridor, sampled locally per stage —
    far politer than 27 point requests."""
    width = max(2, int((bbox["east"] - bbox["west"]) * px_per_deg))
    height = max(2, int((bbox["north"] - bbox["south"]) * px_per_deg))
    query = {
        "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap",
        "LAYERS": layer, "STYLES": "", "SRS": "EPSG:4326",
        "BBOX": f"{bbox['west']},{bbox['south']},{bbox['east']},{bbox['north']}",
        "WIDTH": str(width), "HEIGHT": str(height),
        "FORMAT": "image/tiff", "TIME": day.isoformat(),
    }
    try:
        r = requests.get(EFFIS_WMS, params=query, timeout=60)
        r.raise_for_status()
        if not r.content.startswith((b"II", b"MM")):
            head = r.content[:150].decode("utf-8", "replace").replace("\n", " ")
            print(f"  ! FWI {layer} {day}: not a raster: {head}")
            return None
        return _tiff_floats(r.content)
    except Exception as exc:
        print(f"  ! FWI {layer} {day}: {exc}")
        return None


def grid_value(grid: tuple[list[float], int, int], bbox: dict,
               lat: float, lon: float) -> float | None:
    values, width, height = grid
    fx = (lon - bbox["west"]) / (bbox["east"] - bbox["west"])
    fy = (bbox["north"] - lat) / (bbox["north"] - bbox["south"])  # rows are top-down
    x = min(width - 1, max(0, int(fx * width)))
    y = min(height - 1, max(0, int(fy * height)))
    idx = y * width + x
    if not 0 <= idx < len(values):
        return None
    v = values[idx]
    # Reject nodata as well as zero. Float32 rasters carry sentinels like 1e20 or
    # 9999, and every one of those classifies as "very extreme" — which inside the
    # walk window would fire an alert-tier push for a pixel with no data in it.
    # Real FWI does not exceed ~150 even in extreme Mediterranean conditions.
    if not (0 < v < FWI_SANE_MAX) or v != v:  # v != v catches NaN
        return None
    return v


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


def fwi_sweep(bbox: dict, stages: list[dict], today: date,
              ledger: dict | None = None) -> list[dict]:
    """Sample the FWI forecast and report the corridor's worst state.

    ONE item per run describing max-over-horizon, not one per forecast day: the
    3-day loop used to emit the same peak three times with a constant URL, which
    was the largest single contributor to duplicate findings.
    """
    ledger = ledger if ledger is not None else {}
    live = WALK_START <= today <= WALK_END
    cereal_season = today.month in CEREAL_RISK_MONTHS

    best: tuple[float, dict, float | None, date] | None = None
    readings_by_day: dict[date, list] = {}
    for offset in range(FWI_FORECAST_DAYS):
        day = today + timedelta(days=offset)
        grid = fetch_fwi_grid(bbox, day, FWI_LAYER)
        if grid is None:
            continue
        FWI_REACHABLE["ok"] = True
        anomaly = fetch_fwi_grid(bbox, day, FWI_ANOMALY_LAYER)
        readings = []
        for stage in stages:
            v = grid_value(grid, bbox, stage["lat"], stage["lon"])
            if v is None or v < FWI_REPORT_FROM:
                continue
            a = grid_value(anomaly, bbox, stage["lat"], stage["lon"]) if anomaly else None
            readings.append((stage, v, a))
        readings_by_day[day] = readings
        print(f"  FWI {day}: {len(readings)}/{len(stages)} stage(s) at "
              f"'high' or worse")
        for stage, v, a in readings:
            if best is None or v > best[0]:
                best = (v, stage, a, day)

    if best is None:
        return []
    worst_v, worst_stage, worst_a, worst_day = best
    all_readings = [r for rs in readings_by_day.values() for r in rs]
    peak_stages = sorted({s["stage"] for s, v, _ in all_readings
                          if v >= FWI_ALERT_FROM})
    breadth = len({s["stage"] for s, _, _ in all_readings})

    # Hysteresis: require crossing the next threshold by +2.0 to escalate and
    # dropping 2.0 below to relax, so 21.0 -> 21.4 -> 21.1 -> 21.5 is ONE event.
    entry = ledger.get("fwi:corridor", {})
    previous = entry.get("fingerprint", "") or ""
    previous_class = previous.split(":")[0] if previous else ""
    cls = fwi_class(worst_v)
    if previous_class and cls != previous_class:
        thresholds = {name: t for t, name in FWI_CLASSES}
        edge = thresholds.get(cls)
        if edge is not None and abs(worst_v - edge) < FWI_HYSTERESIS:
            cls = previous_class  # too close to the edge to call it a change
    band = "1-2" if breadth <= 2 else ("3-8" if breadth <= 8 else "9+")
    fingerprint = f"{cls}:{band}"
    rank = [n for _, n in FWI_CLASSES][::-1].index(cls) if cls in [
        n for _, n in FWI_CLASSES] else 0

    emit, why = cl.should_emit(ledger, "fwi:corridor", fingerprint, rank, today)
    if not emit:
        print(f"  · FWI {fingerprint}: {why}, not re-emitted")
        return []

    stage_list = ", ".join(
        f"{s['end']} (stage {s['stage']}) {v:.0f}"
        for s, v, _ in sorted(all_readings, key=lambda r: -r[1])[:6])
    cereal = [s for s, _, _ in all_readings if s["stage"] >= 7]
    notify = "alert" if (worst_v >= FWI_ALERT_FROM and (live or cereal_season)) \
        else "quiet"
    anom = ""
    if worst_a is not None:
        anom = (f" FWI anomaly at {worst_stage['end']} is {worst_a:+.1f} "
                f"versus the seasonal norm.")
    harvest = ""
    if cereal and cereal_season:
        harvest = (" These are cereal stages — sustained high FWI in spring is "
                   "what advances the harvest out of mid-June and puts machinery "
                   "in dry fields while the walk is on.")
    return [{
        "source_name": "EFFIS / Copernicus Fire Weather Index forecast",
        "url": "https://forest-fire.emergency.copernicus.eu/apps/fire.risk.viewer/",
        "region": worst_stage["region"],
        "tier": "official",
        "lang": "en",
        "weight": 1.2 if notify == "alert" else 0.9,
        "notify": notify,
        "kind": "fire_weather",
        "item_key": "fwi:corridor",
        "fingerprint": fingerprint,
        "stage": worst_stage["stage"],
        "stage_end": worst_stage["end"],
        "text": (
            f"EFFIS Fire Weather Index peaks at {worst_v:.1f} — '{cls}' on the "
            f"EFFIS scale — at {worst_stage['end']} (stage {worst_stage['stage']}) "
            f"on {worst_day.isoformat()}, across a {FWI_FORECAST_DAYS}-day forecast "
            f"from {today.isoformat()}. {breadth} stage endpoint(s) reach 'high' "
            f"(>= {FWI_REPORT_FROM}) or worse: {stage_list}."
            + (f" At 'very high' or above: stages {peak_stages}." if peak_stages else "")
            + f"{anom}{harvest} Reported because: {why}. Classes: low <11.2, "
            f"moderate 11.2-21.3, high 21.3-38, very high 38-50, extreme 50-70, "
            f"very extreme >70."
        ),
    }]


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


def note_health(**kw) -> None:
    """Merge this script's liveness into state/health.json (shared with the others)."""
    try:
        with open(HEALTH_FILE, encoding="utf-8") as fh:
            health = json.load(fh)
    except Exception:
        health = {}
    health.update(kw)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(HEALTH_FILE, "w", encoding="utf-8") as fh:
            json.dump(health, fh, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception as exc:
        print(f"  · could not update health.json: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7,
                    help="lookback window for the live sweep (max 10)")
    ap.add_argument("--retro", nargs="*", type=int, metavar="YEAR",
                    help="historical Jun-Sep sweep for the given years")
    ap.add_argument("--check", action="store_true",
                    help="probe whether the FIRMS API is serving, then exit")
    ap.add_argument("--no-fwi", action="store_true",
                    help="skip the EFFIS Fire Weather Index forecast sweep")
    args = ap.parse_args()

    os.makedirs(STATE_DIR, exist_ok=True)
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    stages = load_stages()
    bbox = load_bbox()
    today = datetime.now(timezone.utc).date()

    if args.check and not key:
        # Diagnostic only: never touch the output file.
        print("FIRMS_MAP_KEY is not set — nothing to check.")
        print("Request a free key at "
              "https://firms.modaps.eosdis.nasa.gov/api/map_key/")
        return 1

    # The FWI forecast is a separate, keyless service — it must still run when
    # FIRMS is unavailable, because it is the earliest warning of the two.
    fwi_items: list[dict] = []
    if not args.no_fwi and args.retro is None and not args.check:
        print(f"[{today}] EFFIS Fire Weather Index forecast "
              f"({FWI_FORECAST_DAYS} days) …")
        fwi_ledger = cl.load()
        fwi_items = fwi_sweep(bbox, stages, today, fwi_ledger)
        cl.save(fwi_ledger)
        # A silent EFFIS outage must be visible: fwi_reachable is set by the
        # sweep, and is False when every grid request failed.
        note_health(fwi_ok=FWI_REACHABLE.get("ok", False),
                    fwi_run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    if not key:
        print("FIRMS_MAP_KEY is not set — skipping satellite fire detection.")
        print("Request a free key at https://firms.modaps.eosdis.nasa.gov/api/map_key/")
        with open(OUT_FILE, "w", encoding="utf-8") as fh:
            json.dump(fwi_items, fh, ensure_ascii=False, indent=2)
        alerts = sum(1 for i in fwi_items if i["notify"] == "alert")
        print(f"{len(fwi_items)} FWI item(s) ({alerts} alert-tier). Wrote {OUT_FILE}")
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
    items = fwi_items + to_items(clusters, mode, today)
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
