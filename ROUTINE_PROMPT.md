# Routine setup — copy/paste

Create the routine at **claude.ai/code/routines → New routine** (or `/schedule` in the
CLI). Use the settings and prompt below.

## Settings

| Field | Value |
|---|---|
| **Repository** | `eriksend/camino-ignaciano-watch` |
| **Trigger** | Schedule → **Daily**, ~07:30 your local time |
| **Environment** | Default (the one you created, **Full** network access) |
| **Connectors** | **Remove all of them.** This routine only reads public web pages and writes to its own repo; it has no reason to touch your M365 / HubSpot / Mem. Stripping connectors is the main safety control for a web-fetching routine. |
| **Branch push** | The routine must commit back to the repo. By default Claude can only push to `claude/*` branches — either relax that for this repo (Settings → repo → allow push to `main`) or let it push to a `claude/watch` branch and point GitHub Pages there. |

## Environment variables

Both optional; set them in the cloud environment's variables.

| Variable | Effect |
|---|---|
| `CAMINO_NTFY_URL` | An [ntfy](https://ntfy.sh) topic URL (e.g. `https://ntfy.sh/your-private-topic`) for phone pushes. Only **`notify: alert`** findings are pushed — quiet ones stay in the report. Unset = no pushes at all. |
| `FIRMS_MAP_KEY` | Free NASA FIRMS key ([request here](https://firms.modaps.eosdis.nasa.gov/api/map_key/)) enabling **satellite fire detection** near the route. Unset = `fetch_fires.py` still runs the EFFIS Fire Weather Index sweep (no key needed) and the run still succeeds; you only lose the satellite half. `fetch_alfa.py` needs no key at all. |

## Prompt

```
Run the camino-watch routine. Follow .claude/skills/camino-watch/SKILL.md exactly.

In brief: from the repo root, install requirements, then run scripts/fetch_sources.py,
scripts/fetch_fires.py and scripts/fetch_alfa.py. For every item in
state/new_items.json, state/fire_items.json AND state/alfa_items.json, translate it to English if needed, write a short title and 1-3
sentence summary, and score its relevance 0-100 per the skill's rubric (applying each
item's weight). Carry each item's notify tier through to the finding unchanged. Append
the results to state/findings.json, de-duplicating by id and marking this run's items
is_new while clearing is_new on older ones. Regenerate the report with
scripts/build_report.py, then commit and push state/, docs/index.html and report.html.
If CAMINO_NTFY_URL is set and any new finding is notify:"alert" and scores >= 35, send
the ntfy digest for those findings only.

Be concise and factual — never invent details, and skip any source that wasn't
reachable. Preserve place names, dates, and prices exactly when translating.
```

That's the whole routine. One run a day stays well within the daily routine allowance
on any paid plan.
