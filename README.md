# Camino Ignaciano watch

A daily monitor for a pilgrimage that produces little online noise — much of it in
Spanish and Catalan. It runs as a **Claude Code routine**: once a day a cloud session
checks a curated set of sources, translates and ranks anything new for a **spring walk**,
logs it, and regenerates a browser-viewable report. No always-on server, no API key — it
runs on your plan's included routine runs.

## How it works

The work is split so each half does what it's good at:

- **`scripts/fetch_sources.py`** — the deterministic half. Fetches each source in
  `sources.yaml`, extracts the main body text (dropping nav/boilerplate), diffs it
  against the stored baseline in `state/`, and writes the genuinely new/changed chunks to
  `state/new_items.json`. No model involved, so it's cheap and reliable. First sight of a
  source is recorded as a baseline and emits nothing (no first-run flood).
- **The routine's Claude session** — the judgment half. Reads `new_items.json`, translates
  Spanish/Catalan/Basque to English, writes a short title and summary, and scores each
  item's relevance to a spring walk. The method is pinned in
  `.claude/skills/camino-watch/SKILL.md`.
- **`scripts/fetch_fires.py`** — wildfire detection, geofenced. Pulls NASA FIRMS VIIRS
  active-fire points for the corridor bounding box, filters them by haversine distance to
  the 27 stage endpoints in `data/end_coords.json` (20 km), and clusters them so one fire
  becomes one finding rather than hundreds of hotspot rows. Writes `state/fire_items.json`.
- **`scripts/fetch_alfa.py`** — Catalan **Pla ALFA** fire-risk levels and natural-space
  closures, from public ArcGIS JSON feeds (no key). Levels 0–2 only govern fire-risk *work*;
  level 3 bans flame and spark-generating machinery but **not** walking; only level 4 can
  close footpaths. Since Montserrat is a natural park and the stage 26 endpoint, a level-4
  closure can block the last two stages — it happened in July 2026. The script trusts the
  closures feed over the level, because spaces have been closed with no municipality at
  level 4. Writes `state/alfa_items.json`.
- **`scripts/preflight.py`** — an offline gate the routine runs *first*. Validates that every
  script imports, `sources.yaml` is coherent (types, weights, regions, dates, no duplicate
  URLs), the 27 stage endpoints are all inside the corridor box *with the 20 km geofence
  margin*, every stage maps to a climate band, and `findings.json` holds no duplicate ids or
  out-of-range scores. Exits non-zero so a broken config stops the run loudly instead of
  quietly producing nothing. It caught a real bug on its first run: two stage endpoints
  (Laguardia, Navarrete) sat *outside* the bounding box used to query fire detections.
- **`scripts/build_report.py`** — renders `state/findings.json` into `docs/index.html`
  (and `report.html`): a static, filterable report styled around the camino's own orange
  waymark, regenerated every run.

State lives in the repo (`state/findings.json`, plus the per-source ledger
`state/sources.json`), so history and de-duplication persist across runs via git.

## Cadence and notification tiers

Checking is nearly free — it's a hash comparison — so the schedule never slows down to save
effort. What's tiered is **how loudly a change is announced**. Each source in `sources.yaml`
can carry:

| field | meaning |
|---|---|
| `check` | `daily` (default), `twice_daily`, `weekly`, or a number of hours |
| `analyze` | `on_change` (default) hands changed text to the model; `never` tracks the hash silently |
| `notify` | `quiet` (default) = report only; `alert` = also pushed to your phone |
| `notify_from` | date on which `quiet` escalates to `alert` (one-way) |
| `dormant_until` | skip the source entirely before this date |
| `stop_after` | stop checking after this date |

The point of `notify_from`: a bus timetable change in late 2026 is genuinely useful signal
about how stable that corridor is, and gets logged quietly — but it shouldn't buzz your
phone until departure is close enough to act on. `state/sources.json` records
`last_checked`, `last_hash` and `last_change_seen` per source so the schedule survives runs.

## Wildfire monitoring

Two jobs, scored differently (the rubric is in the skill):

- **Aftermath** (now → departure, `quiet`) — did fire burn on or near the route? Damage to
  waymarking, tracks, shade, bridges and lodging outlasts the fire by months, so a past
  burn scar is a live planning concern.
- **Live risk** (during the walk, 2027-04-19 → ~2027-05-20, `alert`) — active fire, fire-risk
  levels and access restrictions. In Catalonia a **Pla ALFA** level can close paths in a
  natural park like Montserrat with no fire burning at all.

Seasonal asymmetry matters: Spain's peak season is Jun–Sep, *outside* the walk window — but
the Basque/Cantabrian north has its own Feb–Apr season from dry föhn winds and agricultural
burning, so fire near stages 1–6 in spring is weighted up.

**Leading indicators matter more than detections here.** The fire that could actually touch
an April–May walk is agricultural — cereal-harvest machinery igniting dry fields in La
Segarra, Urgell, the Lleida plain, Monegros and the Ebro plain. Barley on the Lleida plain
normally harvests from **~20 May**, so a ~20 May finish has **no margin at all** — the walk's
final stages already fall inside the normal barley harvest, and a hot dry spring moves
machinery into the middle of the walk rather than merely closer to it. The
monitor therefore watches antecedent conditions: the **EFFIS Fire Weather Index** forecast
sampled at all 27 stage endpoints (plus its anomaly-vs-normal layer), AEMET drought and
precipitation anomalies judged against the planner's own 1991–2020 normals in
`data/climate_bands.json`, and any reporting of an early harvest. An observed early harvest
outranks a drought anomaly, which outranks an FWI forecast.

FWI needs **no API key** and runs even when the satellite half is switched off. Satellite
detection does need a free NASA FIRMS map key
([request one](https://firms.modaps.eosdis.nasa.gov/api/map_key/)) set as `FIRMS_MAP_KEY` in
the routine's environment; without it `fetch_fires.py` still does the FWI sweep and exits
cleanly, so the daily run never breaks.

## Setup

1. Push this repo to `eriksend/camino-ignaciano-watch`.
2. Create the routine — see **`ROUTINE_PROMPT.md`** for the exact prompt and the
   recommended schedule / environment / connector / branch settings.
3. Decide how you'll read the report (below).

## Reading the report

- **Public repo:** enable GitHub Pages (Settings → Pages → `main` / `docs`) for a clean URL
  like `https://eriksend.github.io/camino-ignaciano-watch/`. Pages on a free account needs
  the repo to be public — fine here, since nothing in it is sensitive.
- **Private repo:** keep it private and just `git pull` and open `report.html`, or view it
  through the Claude Code file browser. (Free-plan Pages doesn't serve private repos.)

## Adding sources

Edit `sources.yaml`. Each entry has a `url`, a `type` (`html` to scrape-and-diff, `rss`
for feeds), a `lang` (`es`/`ca`/`eu` get translated, `en` doesn't), a `region` and `tier`
for filtering, and a `weight` that nudges relevance. Copying a Google News RSS line and
changing the query is the quickest way to widen coverage. The optional scheduling and
notification fields are in the table above.

## Running it by hand

You can run the mechanical parts locally without the routine:

```
pip install -r requirements.txt
python scripts/fetch_sources.py     # writes state/new_items.json
python scripts/fetch_fires.py       # writes state/fire_items.json (FWI + FIRMS)
python scripts/fetch_alfa.py        # writes state/alfa_items.json (Pla ALFA)
python scripts/build_report.py      # rebuilds the report from findings.json

python scripts/fetch_fires.py --check            # is the FIRMS API serving?
python scripts/fetch_fires.py --retro 2025 2026  # historical Jun-Sep sweep
python scripts/fetch_fires.py --no-fwi           # skip the FWI sweep
python scripts/fetch_alfa.py --show              # print ALFA state, write nothing
```

Translation and scoring only happen inside the routine (that's where the model is), so a
purely local run will fetch and report but won't fill in summaries.

## Tests

`tests/` holds ~95 pytest cases over the pure logic — date gating, the change-memory state
machine, the GeoTIFF parser, the geofence, the report's badges and the health classifier.
Install with `pip install -r requirements-dev.txt` and run `python -m pytest -q`.

**These are a development tool, not a safety net for the daily run.** Nothing schedules them,
and by design they never touch the network — which means they cannot catch this project's
most likely failure: the world changing underneath it. A test asserting the EFFIS layer is
called `mf010.fwi` passes forever whether or not Copernicus still serves it. That job belongs
to `preflight.py` and the health panel; the tests exist to stop *edits* breaking working logic.

## Monitor health

Every run records per-source outcomes in `state/sources.json` and a run summary in
`state/health.json`, and the report renders both as a panel at the top. It distinguishes
**FAILING** (the last fetch attempt errored) from **STALE** (not being checked at all) from
**SILENT** (fetching fine, simply unchanged — normal for a static page). That distinction is
the whole point: without it a dead source produces output identical to a quiet one.

## A note on courtesy

One fetch per source per run, a real User-Agent, a one-second pause between sources. Daily
is plenty for a route this quiet — please don't crank the schedule to high frequency.
