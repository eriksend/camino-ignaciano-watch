---
name: camino-watch
description: >
  Run the Camino Ignaciano monitoring routine. Use when a scheduled routine (or a
  manual session) needs to check the curated sources for new information about the
  Ignatian Camino, translate non-English finds, score their usefulness for planning
  a spring walk, update the findings log, regenerate the browser report, and commit.
---

# Camino Ignaciano watch — routine methodology

You are maintaining a monitor for the **Camino Ignaciano** (Ignatian Way), a ~650 km
pilgrimage from Loyola to Manresa. The reader is planning to walk it in **spring** and
cares about anything that affects that walk. Your job each run: turn raw new content
into ranked, English, planning-ready findings, refresh the report, and commit.

Work from the repository root. Be concise, defensible, and never fabricate — if a
source was unreachable it simply won't appear in the input; skip it silently.

## Procedure

1. **Install + fetch.** Run:
   ```
   pip install -r requirements.txt
   python scripts/fetch_sources.py
   python scripts/fetch_fires.py
   ```
   `fetch_sources.py` writes `state/new_items.json` (new/changed chunks) and updates the
   ledger `state/sources.json`. `fetch_fires.py` writes `state/fire_items.json` (satellite
   fire detections near the route; empty and harmless if `FIRMS_MAP_KEY` is unset).
   Neither script translates or scores — that is your job.

2. **Load both** `state/new_items.json` **and** `state/fire_items.json`, and process the
   concatenation. Each item has: `source_name`, `url`, `region`, `tier`, `lang`, `weight`,
   `notify`, `text`; fire items also carry `kind`. If both lists are empty, skip to step 6
   (still regenerate the report) and finish, noting "no new items this run."

3. **For each item, produce a finding.** Read the `text`:
   - **Translate** to English if `lang` is not `en`. Preserve place names
     (Loyola/Loiola, Azpeitia, Logroño, Verdú, Monegros, Montserrat, Manresa),
     numbers, dates, and prices exactly. Don't editorialize or add facts.
   - Write `title`: a short English headline, ≤ 90 characters.
   - Write `summary_en`: 1–3 sentences, plain and factual.
   - Score `relevance` 0–100 using the rubric below, then multiply by the item's
     `weight` and clamp to 100 (round to an integer).
   - **Carry `notify` through unchanged** from the item to the finding. Never invent or
     upgrade it — the tier is set by `sources.yaml` and the fire script, not by you.

4. **Append with dedup.** Compute `id` = first 16 hex chars of sha1(`url` + `title`).
   Load `state/findings.json` (treat a missing file as `[]`). First set `is_new: false`
   on every existing finding. Then add each new finding with `is_new: true`, skipping any
   whose `id` already exists. Keep the newest ~500 findings. Write `state/findings.json`
   (UTF-8, `ensure_ascii=False`, indented).

5. **Schema** for each finding object:
   ```json
   {
     "id": "16-hex",
     "detected_at": "ISO-8601 UTC",
     "source_name": "…", "url": "…",
     "region": "basque|rioja|navarre|aragon|catalonia|whole",
     "tier": "official|guide|forum|blog|town|tour|social|discovered",
     "lang": "es|ca|eu|en",
     "title": "English headline",
     "summary_en": "1-3 sentence English summary",
     "source_text": "verbatim text from the item (the raw `text` field from new_items.json)",
     "relevance": 0,
     "notify": "quiet|alert",
     "kind": "fire_live|fire_aftermath   (fire items only; omit otherwise)",
     "stage": 24, "stage_end": "Cervera",
     "is_new": true
   }
   ```
   `source_text` is the verbatim extracted/changed text that was scored — copy it from the
   item's `text` field unchanged. This lets the report show a collapsible cached copy.
   `stage`/`stage_end` are optional; set them when an item is tied to a specific stage
   (fire items name the stage in their text — carry it over).

6. **Rebuild the report:** `python scripts/build_report.py` (writes `docs/index.html`
   and `report.html`).

7. **Commit and push** the changed files (`state/`, `docs/index.html`, `report.html`)
   with a message like `watch: N new finding(s) YYYY-MM-DD`.

8. **Alert (optional, connector-free).** Push only for **`notify: "alert"`** findings —
   that is the whole point of the tiers. If `CAMINO_NTFY_URL` is set and at least one
   *new* finding is `notify: "alert"` **and** scored ≥ 35, POST a short digest of those
   (by relevance) with `curl`, e.g.
   `curl -H "Title: Camino Ignaciano: N alert(s)" -d "<digest>" "$CAMINO_NTFY_URL"`.
   Never include `quiet` findings in the push — they belong in the report only. If the
   variable isn't set, or nothing new is alert-tier, skip alerting silently.

## Notification tiers

Checking is nearly free (a hash comparison), so cadence is never reduced to save effort —
**notification** is what's tiered instead. `sources.yaml` carries per-source `check`,
`analyze`, `notify`, `notify_from`, `dormant_until` and `stop_after`; `fetch_sources.py`
resolves them and stamps each item with an effective `notify`. A bus timetable change in
late 2026 is real signal about corridor instability and should be logged quietly; it only
becomes worth a phone buzz closer to departure, which `notify_from` handles automatically.

Consequences for you: a `quiet` finding is still written, still scored, still shown in the
report — it just never reaches the push digest. Don't skip or under-score quiet items.

## Relevance rubric

- **70–100 (high):** accommodation opening/closing/price changes; route or waymarking
  changes; pilgrim-credential changes; season/weather notes tied to specific stages;
  water or safety on remote stages (e.g. Monegros); new official guide editions or
  corrections; transport access to start/end points.
- **40–69 (medium):** firsthand trip reports; opening hours of sanctuaries/La Cova;
  GPS-track updates; general spring-walking advice.
- **0–39 (low):** generic marketing, tour-package promotions, restated information
  already known, off-topic forum chatter. (Apply the weight, then let low scores fall
  below the alert threshold rather than discarding them.)

## Wildfire scoring

Fire monitoring does **two different jobs**, and the same detection means different things
depending on when it happened. Decide which job applies before scoring.

**(a) Aftermath — now until departure.** Did fire burn on or near the route? Damage to
waymarking, tracks, shade, bridges and pilgrim lodging persists for *months* after the
flames are out, so a 2025 or 2026 burn scar is a live planning concern even though the fire
is long over. Low urgency, high value. `notify: quiet`. Score on **damage to the walk**:
- **70–100:** burn crossed or reached the waymarked route; signage/marking reported
  destroyed or unreliable; a bridge, water point or pilgrim lodging damaged or closed;
  a stage rerouted because of fire damage.
- **40–69:** burn within a few km of the route without confirmed route damage; loss of tree
  cover/shade on an exposed stage; a firsthand report describing post-fire conditions.
- **0–39:** fire in the wider region with no plausible route bearing; a detection whose
  nearest stage endpoint is >15 km away with nothing else to tie it to the corridor.

**(b) Live risk — during the walk (2027-04-19 to ~2027-05-20).** Active fire, fire-risk
levels and access restrictions on stages about to be walked. `notify: alert`. Score high
and be blunt; an access restriction with no fire at all can still block a stage. Treat
**Pla ALFA** level (Catalonia) and Aragón's fire-risk/`época de peligro alto` restrictions
as route-blocking risks in their own right — Montserrat is a natural park and can close
paths on ALFA level alone.

**Seasonal asymmetry — this matters and is easy to get backwards.** The walk is 19 Apr –
~20 May 2027, and Spain's peak fire season is Jun–Sep, so *overall* live fire risk in the
walk window is low. The exception: the **Basque/Cantabrian north has a distinct
late-winter/spring fire season (Feb–Apr)** driven by dry föhn winds and agricultural
burning, which overlaps the start of the walk. So:
- Fire activity near **stages 1–6** (Zumárraga → Laguardia) during **Feb–May**: score
  *higher*, and treat as a live hazard.
- Fire news on the **Monegros (stages ~17–20)** and **Catalan (stages 21–27)** stages:
  outside Jun–Sep, treat as an **aftermath/damage** question, not a live hazard.

`fetch_fires.py` already applies this: it geofences NASA FIRMS detections to the 27 stage
endpoints in `data/end_coords.json` (20 km radius, haversine), clusters them so one fire is
one item, and pre-sets `notify` and `kind`. Trust its `kind` unless the item's own text
contradicts it, and always name the affected stage(s) in your title.

## Optional discovery step

After processing the list, you may run **one** web search for recent
"Camino Ignaciano" / "Ignatian Way" news to catch sources not in `sources.yaml`. Add
only genuinely new, relevant results as findings with `tier: "discovered"`. Don't let
discovery balloon the run — a single search, a few finds at most.
