#!/usr/bin/env python3
"""
fetch_alfa.py — Catalan Pla ALFA fire-risk levels and, crucially, the closures
that can block a stage with no fire burning anywhere near it.

WHAT THE LEVELS ACTUALLY DO (verified against the official Agents Rurals page,
https://interior.gencat.cat/ca/arees_dactuacio/agents-rurals/pla-alfa/):

  levels 0-2  a permit/notification regime for fire-RISK ACTIVITIES
              (agricultural and forestry work). Nothing restricts walkers.
  level 3     open flame banned everywhere; authorised stubble/pasture/pruning
              burns suspended; spark-generating machinery banned on forest land
              and within 500 m. Still NOTHING about access on foot.
  level 4     "Es pot restringir l'accés als espais naturals protegits i altres
              zones forestals d'alta freqüentacio" — the only level that can shut
              footpaths, and it is discretionary, enacted by signed resolution.

So a pilgrim on a marked path breaks no rule at level 3. Level 4 is the
route-blocking level. Montserrat is a natural park and the stage 26 endpoint:
in July 2026 its paths were closed under level 4 and reopened on 25 July, while
the rack railway, cable car and funiculars kept running — the monastery stayed
reachable mechanically even though the walking stages were gone.

Because of that, this script trusts the CLOSURES layer over any level inference.
Today's data shows spaces closed while no municipality sits at level 4, so
deriving closure state from the level would miss real closures.

Writes state/alfa_items.json. No API key needed.

Usage:
    python scripts/fetch_alfa.py
    python scripts/fetch_alfa.py --show      # print the picture, write nothing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import requests

import condition_ledger as cl
from walk_window import in_walk_window, near_walk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, "state")
OUT_FILE = os.path.join(STATE_DIR, "alfa_items.json")

ARCGIS = ("https://services7.arcgis.com/ZCqVt1fRXwwK6GF4/arcgis/rest/services"
          "/{service}/FeatureServer/{layer}/query")
# Municipal layer is authoritative for levels. The comarcal layer is NOT usable:
# it returns PERILL=5 for all 43 comarques while the municipal layer shows 0-2
# for the same territory, and its timestamps update, which makes the bad values
# look fresh rather than obviously broken.
LEVELS_TODAY = ("Pla_Alfa_Municipal_Avui_FL_2_view", 0)
LEVELS_TOMORROW = ("pla_alfa_municipal_dema_FL_VW", 5)
CLOSURES_TODAY = ("tancaments_pla_alfa_avui_VW", 2)
CLOSURES_TOMORROW = ("tancaments_pla_alfa_dema_VW", 2)

# Comarques the Catalan stages cross. Baix Llobregat is not an obvious route
# comarca but Collbato is the Montserrat gateway, so it belongs here.
ROUTE_COMARQUES = {
    "Segrià": [21], "Pla d'Urgell": [22], "Urgell": [23], "Segarra": [24],
    "Anoia": [25, 26], "Baix Llobregat": [26], "Bages": [26, 27],
}
# Municipalities that carry the walk itself, for naming in the output.
ROUTE_MUNICIPIS = {
    "Lleida": 21, "El Palau d'Anglesola": 22, "Verdú": 23, "Cervera": 24,
    "Igualada": 25, "El Bruc": 26, "Collbató": 26,
    "Monistrol de Montserrat": 26, "Marganell": 26, "Manresa": 27,
}
# Closure entries whose name implies a route stage is shut. Matched case- and
# accent-loosely against the Espai_prot field.
ROUTE_SPACES = {
    "montserrat": (26, "Montserrat"),
    "sant llorenc": (27, "Sant Llorenç del Munt (near Manresa)"),
    "serra d'obac": (27, "Sant Llorenç del Munt i l'Obac"),
}
LEVEL_REPORT_FROM = 3    # below 3 nothing is even prohibited outdoors
TIMEOUT = 45


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def query(service: str, layer: int, where: str = "1=1",
          fields: str = "*") -> list[dict] | None:
    url = ARCGIS.format(service=service, layer=layer)
    params = {"where": where, "outFields": fields, "returnGeometry": "false",
              "f": "json", "resultRecordCount": 2000}
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:
        print(f"  ! {service}: {exc}")
        return None
    if "error" in payload:
        print(f"  ! {service}: {payload['error'].get('message', payload['error'])}")
        return None
    return [f.get("attributes", {}) for f in payload.get("features", [])]


def fold(text: str) -> str:
    """Loose match key: lowercase, accents stripped, punctuation dropped."""
    out = []
    table = str.maketrans("àáâäãèéêëìíîïòóôöõùúûüçñ", "aaaaaeeeeiiiiooooouuuucn")
    for ch in (text or "").lower().translate(table):
        out.append(ch if ch.isalnum() else " ")
    return " ".join("".join(out).split())


def levels_for_route(rows: list[dict]) -> dict:
    """Aggregate the municipal layer ourselves, since the comarcal layer lies."""
    by_comarca: dict[str, int] = {}
    hot_municipis: list[tuple[str, str, int]] = []
    for row in rows:
        comarca = row.get("NOMCOMAR") or ""
        if comarca not in ROUTE_COMARQUES:
            continue
        try:
            level = int(row.get("PERIL_M") or 0)
        except (TypeError, ValueError):
            continue
        by_comarca[comarca] = max(by_comarca.get(comarca, 0), level)
        name = row.get("NOMMUNI") or ""
        if level >= LEVEL_REPORT_FROM:
            hot_municipis.append((name, comarca, level))
    hot_municipis.sort(key=lambda x: -x[2])
    return {"by_comarca": by_comarca, "hot": hot_municipis}


def route_closures(rows: list[dict]) -> list[dict]:
    hits = []
    for row in rows:
        name = row.get("Espai_prot") or row.get("ESPAI_PROT") or ""
        key = fold(name)
        for needle, (stage, label) in ROUTE_SPACES.items():
            if needle in key:
                hits.append({"space": name, "stage": stage, "label": label})
                break
    return hits


def build_items(today: date, levels: dict, closures: list[dict],
                tomorrow_levels: dict, tomorrow_closures: list[dict],
                all_closures: list[str], ledger: dict) -> list[dict]:
    items: list[dict] = []
    escalate = near_walk(today)
    walking = in_walk_window(today)

    # 1. Closures affecting a route stage — the route-blocking case.
    # One condition per space. `when` is deliberately NOT part of the identity:
    # a closure announced for tomorrow used to re-emit tomorrow as "today".
    seen_spaces = {}
    for c in closures:
        seen_spaces[fold(c["space"])] = dict(c, when="today")
    for c in tomorrow_closures:
        seen_spaces.setdefault(fold(c["space"]), dict(c, when="tomorrow"))
    for fkey, c in sorted(seen_spaces.items()):
        when = c.get("when", "today")
        emit, why = cl.should_emit(ledger, f"alfa:closure:{fkey}", "closed", 4, today)
        if not emit:
            print(f"  · closure {c['space']}: {why}, not re-emitted")
            continue
        items.append({
            "source_name": "Pla ALFA — natural-space closures (Agents Rurals)",
            "url": "https://interior.gencat.cat/ca/arees_dactuacio/agents-rurals/pla-alfa/",
            "region": "catalonia", "tier": "official", "lang": "ca",
            "weight": 1.5 if escalate else 1.0,
            "notify": "alert" if escalate else "quiet",
            "kind": "route_block",
            "stage": c["stage"], "stage_end": c["label"],
            "text": (
                f"Pla ALFA lists the natural space '{c['space']}' as CLOSED to public "
                f"access {when}. This covers stage {c['stage']} ({c['label']}). "
                f"A closure here blocks the walking route even with no fire nearby: in "
                f"July 2026 Montserrat's paths were shut under ALFA level 4 while the "
                f"rack railway, cable car and funiculars kept running, so the monastery "
                f"stayed reachable mechanically but the walking stages did not. "
                f"{'The walk is underway — re-plan this stage.' if walking else 'Outside the walk window: precedent, not an obstacle yet.'}"
            ),
        })

    # 2. Elevated levels on route comarques, for today AND tomorrow. Tomorrow's
    #    layer is the point of the exercise: one day's warning to re-plan a stage.
    #    Level 3 bans flame and machinery but NOT walking, so it is reported
    #    without being dressed up as a blockage.
    for when, snapshot in (("today", levels), ("tomorrow", tomorrow_levels)):
        hot = snapshot.get("hot", [])
        peak = max(snapshot.get("by_comarca", {}).values(), default=0)
        if peak < LEVEL_REPORT_FROM or not hot:
            continue
        names = ", ".join(f"{m} ({c}, level {l})" for m, c, l in hot[:8])
        at_four = sorted({c for _, c, l in hot if l >= 4})
        blocking = peak >= 4
        # Fingerprint excludes `names` on purpose: that list of up to 8
        # municipalities is re-derived daily and churns while the level holds.
        comarques = ",".join(sorted(snapshot.get("by_comarca", {})))
        emit, why = cl.should_emit(
            ledger, f"alfa:level:{when}", f"{peak}:{comarques}", peak, today)
        if not emit:
            print(f"  · level {peak} ({when}): {why}, not re-emitted")
            continue
        items.append({
            "source_name": "Pla ALFA — daily fire-risk level (Agents Rurals)",
            "url": "https://interior.gencat.cat/ca/arees_dactuacio/agents-rurals/pla-alfa/",
            "region": "catalonia", "tier": "official", "lang": "ca",
            "weight": 1.4 if (escalate and blocking) else 1.0,
            "notify": "alert" if (escalate and blocking) else "quiet",
            "kind": "route_block" if blocking else "fire_weather",
            "text": (
                f"Pla ALFA reaches level {peak} on route comarques {when}"
                + (f", including level 4 in {', '.join(at_four)}" if at_four else "")
                + f". Municipalities at level {LEVEL_REPORT_FROM}+: {names}. "
                + ("Level 4 is the only level that can restrict access on foot to "
                   "protected natural areas and heavily-frequented forest, and it is "
                   "discretionary — applied by signed resolution — so a level 4 does "
                   "not by itself mean a stage is shut. Check the closures list, which "
                   "is authoritative for that."
                   if blocking else
                   "Level 3 prohibits open flame everywhere, suspends authorised "
                   "stubble/pasture/pruning burns, and bans spark-generating machinery "
                   "on forest land and within 500 m — but it does NOT restrict access "
                   "on foot. A pilgrim on a marked path breaks no level-3 rule.")
            ),
        })

    # 3. Closures elsewhere in Catalonia: context for how often this is invoked.
    off_route = [n for n in all_closures
                 if not any(k in fold(n) for k in ROUTE_SPACES)]
    if off_route and not closures:
        emit, _ = cl.should_emit(ledger, "alfa:closures:off_route",
                                 ",".join(sorted(off_route)), 1, today)
        if not emit:
            return items
        items.append({
            "source_name": "Pla ALFA — natural-space closures (Agents Rurals)",
            "url": "https://interior.gencat.cat/ca/arees_dactuacio/agents-rurals/pla-alfa/",
            "region": "catalonia", "tier": "official", "lang": "ca",
            "weight": 0.6, "notify": "quiet", "kind": "fire_weather",
            "text": (
                f"Pla ALFA currently closes {len(off_route)} natural space(s) in "
                f"Catalonia, none on the route: {', '.join(off_route[:10])}. "
                f"Recorded as a base rate for how often access closures are invoked."
            ),
        })
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true",
                    help="print the picture without writing state")
    args = ap.parse_args()
    os.makedirs(STATE_DIR, exist_ok=True)
    today = datetime.now(timezone.utc).date()

    print(f"[{today}] Pla ALFA levels and closures …")
    fields = "NOMMUNI,NOMCOMAR,PERIL_M"
    rows = query(*LEVELS_TODAY, fields=fields)
    rows_t = query(*LEVELS_TOMORROW, fields=fields) or []
    closed = query(*CLOSURES_TODAY) or []
    closed_t = query(*CLOSURES_TOMORROW) or []

    if rows is None:
        print("  ! level layer unavailable; writing no items")
        if not args.show:
            with open(OUT_FILE, "w", encoding="utf-8") as fh:
                json.dump([], fh)
        return 1

    levels = levels_for_route(rows)
    levels_t = levels_for_route(rows_t)
    names_closed = [c.get("Espai_prot") or c.get("ESPAI_PROT") or "" for c in closed]
    route_hits = route_closures(closed)
    route_hits_t = route_closures(closed_t)

    print(f"  {len(rows)} municipality row(s); route comarques: "
          + ", ".join(f"{k}={v}" for k, v in sorted(levels['by_comarca'].items())))
    print(f"  closures today: {len(closed)}"
          + (f" -> {', '.join(names_closed[:8])}" if names_closed else ""))
    print(f"  closures affecting the route: {len(route_hits)}"
          + (f" !! {', '.join(h['space'] for h in route_hits)}" if route_hits else ""))
    if levels_t.get("by_comarca"):
        print("  tomorrow: " + ", ".join(f"{k}={v}" for k, v in
                                         sorted(levels_t['by_comarca'].items())))

    ledger = cl.load()
    # A route closure that has lifted is itself news while walking.
    for key in [k for k in ledger if k.startswith("alfa:closure:")]:
        if not any(fold(h["space"]) == key.split(":", 2)[2] for h in
                   route_hits + route_hits_t):
            if cl.clear(ledger, key, today):
                print(f"  · {key} cleared — reporting the reopening")
    items = build_items(today, levels, route_hits, levels_t, route_hits_t,
                        names_closed, ledger)
    items = cl.cap_alerts(items)
    if args.show:
        print(json.dumps(items, ensure_ascii=False, indent=2)[:1500])
        return 0
    with open(OUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=2)
    cl.save(ledger)
    alerts = sum(1 for i in items if i["notify"] == "alert")
    print(f"{len(items)} item(s) ({alerts} alert-tier). Wrote {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
