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
     "kind": "fire_live|fire_aftermath|fire_weather|route_block   (omit if none apply)",
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

### Leading indicators — the risk that actually applies to this walk

The fire that could touch this walk is **agricultural, not forest**: cereal-harvest
machinery igniting dry stubble or standing grain in **La Segarra, Urgell, the Lleida plain,
the Monegros and the Ebro plain** (climate bands `b3`, `b4`, `b5`). The protective fact is
timing — normal cereal harvest there runs **mid-June onward**, i.e. *after* a ~20 May
finish. The whole risk is that **a hot, dry spring advances the harvest** into the walk
window. So the highest-value fire signal is not a detection at all; it is the antecedent
condition that predicts an early harvest.

Score these as **`kind: "fire_weather"`** and reason along that chain explicitly — an item
is only worth a high score if it moves the harvest date, not merely because it says "dry":

- **Fire Weather Index (FWI)** running high for the route provinces in Apr–May 2027 →
  escalate. FWI is a forecast, so it is the earliest warning available. `fetch_fires.py`
  samples it at all 27 endpoints and hands you items already tagged `kind: "fire_weather"`,
  with the class scale in the text (low <11.2, moderate 11.2–21.3, high 21.3–38, very high
  38–50, extreme 50–70, very extreme >70). It only emits at "high" and above, and only
  marks `alert` inside the walk window or the Apr–Jun cereal season — so a high summer
  reading arriving in, say, July is genuinely a quiet item. Don't re-escalate it.
- **Drought / soil-moisture / spring precipitation anomalies.** Judge these against
  `data/climate_bands.json`, which holds the planner's own 1991–2020 normals per band. A
  bare percentage means nothing on its own; convert it. "Lleida at 40% of normal spring
  precipitation" means band `b4` April fell from ~40 mm to roughly 16 mm — say so, in mm,
  and name the band. An anomaly that leaves rainfall inside the normal range is a **low**
  score even though the word "drought" appears.
- **Any reporting of an unusually early or advanced cereal harvest** in the Lleida plain,
  Urgell, La Segarra or Monegros → this is the indicator the others only predict, so score
  it **high** (70+). Barley ripens before wheat, so "barley harvest started" is the earlier
  tell. If a report puts machinery in the fields before ~1 June, that directly overlaps the
  walk and should be called out as such.

Order of confidence when they disagree: an observed early harvest beats a drought anomaly,
which beats an FWI forecast. Don't stack three restatements of the same dry spring into
three high scores — score the strongest once and note the others corroborate it.

### Known fire history — don't re-litigate or conflate these

Settled by research, already in `findings.json`. If a source mentions either, cross-reference
rather than treating it as new:

- **1 July 2025, Torrefeta i Florejacs (La Segarra)** — 5,577.50 ha (3,995 agricultural,
  1,536 forest), 50 km perimeter, **two deaths**, Catalonia's first "sixth-generation" fire.
  It ran **north/northwest** through Oliola, Cabanabona and Vilanova de l'Aguda, **26–28 km
  from the Cervera endpoint**, and **did not reach the waymarked route**. No post-fire reports
  of damaged waymarking, tracks, shade, bridges or lodging were found — that is an absence of
  evidence, not a verified all-clear, so a credible route-damage report from this area would
  still be a real finding.
- **7 July 2026, same municipality** — a separate, much smaller **~113 ha** agricultural fire
  near Selvanera; confinements in Biosca, Sanaüja, Massoteres; no deaths.

**These are two different fires at the same place, a year apart.** Never merge them, and be
careful with the area figure: 5,577 ha is the 2025 fire. Confinement reporting naming
Massoteres or Biosca belongs to the 2026 event.

Note what the 2025 fire demonstrates, because it supports the whole leading-indicator thesis
above: it was **72% agricultural land, igniting 1 July** — right as harvest gets under way —
and it moved at up to 28 km/h. That is the failure mode this monitor is watching for, and it
is why an advanced harvest matters more than a forest-fire forecast.

### Route-blocking restrictions are a separate category

An access restriction can end a stage with **no fire burning anywhere near it**. Score these
as **`kind: "route_block"`** and keep them distinct from fire proximity — they are a
different failure mode and the report badges them separately.

- **Catalonia — Pla ALFA.** Levels 2–3 can restrict access to forest and natural areas.
  **Montserrat is a natural park** (Parc Natural de la Muntanya de Montserrat) and is the
  stage 26 endpoint, so an ALFA restriction can block the **Igualada → Montserrat** climb or
  the **Montserrat → Manresa** approach — the last two stages of the walk. When an ALFA
  level is reported, state which level, which comarques (Anoia, Bages, Segarra, Urgell), and
  whether park access is actually restricted or merely elevated.
- **Aragón — INFOAR / *época de peligro alto*** can restrict access to forest areas and
  regulate agricultural machinery and stubble burning during high risk. Relevant to the
  Monegros crossing (stages ~17–20).

A confirmed closure on a stage the walker must pass is **90+** regardless of season. An
elevated risk level with no access consequence is **40–60**: real, but not yet blocking.
Say plainly which of the two it is — "ALFA 2 declared" and "Montserrat paths closed" are
very different findings and must not be blurred into one.

## Optional discovery step

After processing the list, you may run **one** web search for recent
"Camino Ignaciano" / "Ignatian Way" news to catch sources not in `sources.yaml`. Add
only genuinely new, relevant results as findings with `tier: "discovered"`. Don't let
discovery balloon the run — a single search, a few finds at most.
