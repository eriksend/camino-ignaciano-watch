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
   ```
   This writes `state/new_items.json` (new/changed chunks) and updates
   `state/source_state.json`. The script does no translation or scoring — that is your job.

2. **Load** `state/new_items.json`. Each item has: `source_name`, `url`, `region`,
   `tier`, `lang`, `weight`, `text`. If the list is empty, skip to step 6 (still
   regenerate the report) and finish, noting "no new items this run."

3. **For each item, produce a finding.** Read the `text`:
   - **Translate** to English if `lang` is not `en`. Preserve place names
     (Loyola/Loiola, Azpeitia, Logroño, Verdú, Monegros, Montserrat, Manresa),
     numbers, dates, and prices exactly. Don't editorialize or add facts.
   - Write `title`: a short English headline, ≤ 90 characters.
   - Write `summary_en`: 1–3 sentences, plain and factual.
   - Score `relevance` 0–100 using the rubric below, then multiply by the item's
     `weight` and clamp to 100 (round to an integer).

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
     "relevance": 0,
     "is_new": true
   }
   ```

6. **Rebuild the report:** `python scripts/build_report.py` (writes `docs/index.html`
   and `report.html`).

7. **Commit and push** the changed files (`state/`, `docs/index.html`, `report.html`)
   with a message like `watch: N new finding(s) YYYY-MM-DD`.

8. **Alert (optional, connector-free).** If the environment variable `CAMINO_NTFY_URL`
   is set and at least one *new* finding scored ≥ 35, POST a short digest of the top
   finds (by relevance) to that URL with `curl`, e.g.
   `curl -H "Title: Camino Ignaciano: N new" -d "<digest>" "$CAMINO_NTFY_URL"`.
   If it isn't set, skip alerting.

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

## Optional discovery step

After processing the list, you may run **one** web search for recent
"Camino Ignaciano" / "Ignatian Way" news to catch sources not in `sources.yaml`. Add
only genuinely new, relevant results as findings with `tier: "discovered"`. Don't let
discovery balloon the run — a single search, a few finds at most.
