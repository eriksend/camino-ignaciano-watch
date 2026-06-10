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
- **`scripts/build_report.py`** — renders `state/findings.json` into `docs/index.html`
  (and `report.html`): a static, filterable report styled around the camino's own orange
  waymark, regenerated every run.

State lives in the repo (`state/findings.json`, `state/source_state.json`), so history and
de-duplication persist across runs via git.

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
changing the query is the quickest way to widen coverage.

## Running it by hand

You can run the mechanical parts locally without the routine:

```
pip install -r requirements.txt
python scripts/fetch_sources.py     # writes state/new_items.json
python scripts/build_report.py      # rebuilds the report from findings.json
```

Translation and scoring only happen inside the routine (that's where the model is), so a
purely local run will fetch and report but won't fill in summaries.

## A note on courtesy

One fetch per source per run, a real User-Agent, a one-second pause between sources. Daily
is plenty for a route this quiet — please don't crank the schedule to high frequency.
