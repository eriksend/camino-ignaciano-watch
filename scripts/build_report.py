#!/usr/bin/env python3
"""
build_report.py — render state/findings.json into a browser-viewable report.

Writes docs/index.html (GitHub Pages-friendly) and report.html at the repo root.
Pure static output: client-side filter buttons, no server. Run after the routine
has updated findings.json:  python scripts/build_report.py
"""
from __future__ import annotations

import html
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FINDINGS = os.path.join(ROOT, "state", "findings.json")
LEDGER = os.path.join(ROOT, "state", "sources.json")
CONDITIONS = os.path.join(ROOT, "state", "conditions.json")
HEALTH = os.path.join(ROOT, "state", "health.json")
OUT_DOCS = os.path.join(ROOT, "docs", "index.html")
OUT_ROOT = os.path.join(ROOT, "report.html")

REGION_COLORS = {
    "basque": "#3f7d57", "rioja": "#7d5ba6", "navarre": "#2f6f86",
    "aragon": "#c9a13b", "catalonia": "#9c3b34", "whole": "#8d8473",
}
LANG_NAMES = {"es": "Spanish", "ca": "Catalan", "eu": "Basque", "en": "English"}


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def translate_url(url: str, lang: str) -> str:
    if lang == "en":
        return url
    return f"https://translate.google.com/translate?sl={lang}&tl=en&u={url}"


STALE_AFTER_H = 48.0      # not CHECKED in this long => the run itself is broken
SILENT_AFTER_D = 90       # checked fine but unchanged: informational, not an error


def _age(stamp: str | None, now: datetime) -> float | None:
    """Hours since an ISO stamp, or None if absent/unparseable."""
    if not stamp:
        return None
    try:
        dt = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 3600.0


def _ago(hours: float | None) -> str:
    if hours is None:
        return "never"
    if hours < 1:
        return "just now"
    if hours < 48:
        return f"{hours:.0f}h"
    return f"{hours / 24:.0f}d"


def source_status(entry: dict, now: datetime) -> tuple[str, str]:
    """Classify one source. Returns (status, explanation).

    The distinction that matters: FAILING means the most recent ATTEMPT failed.
    SILENT means it is fetching fine and simply hasn't changed — which for a
    static page is normal and must not be dressed up as a problem.
    """
    err = _age(entry.get("last_error"), now)
    ok = _age(entry.get("last_ok"), now)
    checked = _age(entry.get("last_checked"), now)
    changed = _age(entry.get("last_change_seen"), now)

    if err is not None and (ok is None or err < ok):
        return "FAILING", (entry.get("last_error_msg") or "last fetch failed")
    if checked is None:
        return "STALE", "never checked"
    if checked > STALE_AFTER_H:
        return "STALE", f"not checked for {_ago(checked)}"
    if changed is None:
        # Freshly baselined: we have simply not been watching long enough to say
        # anything. Calling this SILENT would flag every source on the first run
        # after migration and make the panel useless on the day it ships.
        return "OK", "baseline recorded, no change seen yet"
    if changed > SILENT_AFTER_D * 24:
        return "SILENT", f"no change in {_ago(changed)}"
    return "OK", f"changed {_ago(changed)} ago"


def health_panel(ledger: dict | None, conditions: dict | None,
                 health: dict | None, now: datetime) -> str:
    """Standing panel: is the monitor itself working, and what is in force?

    This exists because a dead source produces output identical to a quiet one.
    Everything here was already being recorded and then discarded.
    """
    if not any([ledger, conditions, health]):
        return ""
    order = {"FAILING": 0, "STALE": 1, "SILENT": 2, "OK": 3}
    rows = []
    for entry in (ledger or {}).values():
        status, why = source_status(entry, now)
        rows.append((order[status], status, entry.get("name") or entry.get("url", "?"),
                     why, _ago(_age(entry.get("last_checked"), now)),
                     entry.get("notify", "quiet")))
    rows.sort(key=lambda r: (r[0], r[2]))
    counts = {k: sum(1 for r in rows if r[1] == k) for k in order}

    bad = counts["FAILING"] + counts["STALE"]
    chips = f'{len(rows)} sources'
    if counts["FAILING"]:
        chips += f' · <b class="bad">{counts["FAILING"]} failing</b>'
    if counts["STALE"]:
        chips += f' · <b class="bad">{counts["STALE"]} stale</b>'
    if counts["SILENT"]:
        chips += f' · {counts["SILENT"]} silent'
    if not bad and rows:
        chips += ' · <b class="good">all reachable</b>'

    src_rows = "".join(
        f'<tr class="st-{s.lower()}"><td><span class="pill {s.lower()}">{s}</span></td>'
        f'<td>{esc(name)}</td><td>{esc(why)}</td><td>{esc(checked)}</td>'
        f'<td>{esc(notify)}</td></tr>'
        for _, s, name, why, checked, notify in rows) or \
        '<tr><td colspan="5">No ledger yet — it is written on the next run.</td></tr>'

    active = [dict(v, key=k) for k, v in sorted((conditions or {}).items())
              if v.get("fingerprint") is not None]
    cond_rows = "".join(
        f'<tr><td>{esc(c["key"])}</td><td><b>{esc(c.get("fingerprint"))}</b></td>'
        f'<td>{esc(_ago(_age(c.get("first_seen"), now)))}</td>'
        f'<td>{esc(c.get("peak_rank"))}</td>'
        f'<td>{esc(_ago(_age(c.get("last_emitted"), now)))} ago'
        f' ({esc(c.get("emit_count", 0))}x)</td></tr>'
        for c in active) or '<tr><td colspan="5">Nothing in force.</td></tr>'

    h = health or {}
    failed = h.get("failed") or []
    run_bits = []
    if h.get("run_at"):
        age = _ago(_age(h.get("run_at"), now))
        run_bits.append(f'sources run {esc(age)}'
                        + ('' if age in ("just now", "never") else ' ago'))
    for label, key in [("new", "new_items"), ("alerts", "alerts"),
                       ("baselined", "baselined"),
                       ("churn suppressed", "rebaselined"),
                       ("not due", "skipped"), ("silent", "changed_quiet")]:
        if h.get(key):
            run_bits.append(f'{esc(h[key])} {label}')
    for key, label in [("fwi_ok", "EFFIS FWI"), ("alfa_ok", "Pla ALFA")]:
        if key in h:
            state = "reachable" if h[key] else "UNREACHABLE"
            cls = "good" if h[key] else "bad"
            run_bits.append(f'{label} <b class="{cls}">{state}</b>')
    run_line = " · ".join(run_bits) or "no run recorded yet"
    if failed:
        run_line += ('<br><span class="bad">unreachable: '
                     + esc(", ".join(failed)) + "</span>")

    return f"""<details class="health"{' open' if bad else ''}>
  <summary>Monitor health — {chips}</summary>
  <div class="hbody">
    <p class="hnote">A dead source produces output identical to a quiet one, so this
      panel exists to tell them apart. <b>FAILING</b> = the last attempt errored.
      <b>STALE</b> = not being checked at all. <b>SILENT</b> = fetching fine, just
      unchanged — normal for a static page.</p>
    <table class="htab"><thead><tr><th>Status</th><th>Source</th><th>Detail</th>
      <th>Checked</th><th>Tier</th></tr></thead><tbody>{src_rows}</tbody></table>
    <h4>Conditions in force</h4>
    <table class="htab"><thead><tr><th>Condition</th><th>State</th><th>Since</th>
      <th>Peak</th><th>Last notified</th></tr></thead><tbody>{cond_rows}</tbody></table>
    <h4>Last run</h4>
    <p class="hnote">{run_line}</p>
  </div>
</details>"""


def card(f: dict) -> str:
    rel = int(f.get("relevance", 0))
    region = f.get("region", "whole")
    lang = f.get("lang", "es")
    color = REGION_COLORS.get(region, "#8d8473")
    new = " is-new" if f.get("is_new") else ""
    hi = " hi" if rel >= 60 else ""
    langcls = "es" if lang in ("es", "ca", "eu") else "en"
    when = esc(f.get("detected_at", ""))[:16].replace("T", " ")
    link = translate_url(f.get("url", "#"), lang)
    alert = " is-alert" if f.get("notify") == "alert" else ""
    kind = f.get("kind", "")
    badges = ""
    if alert:
        badges += '<span class="alerttag">alert</span>'
    if kind == "route_block":
        badges += '<span class="blocktag">route blocked</span>'
    elif kind == "fire_weather":
        badges += '<span class="wxtag">fire weather</span>'
    stage = f.get("stage")
    stage_chip = (f'<span class="chip stage">stage {esc(stage)}'
                  f'{" · " + esc(f["stage_end"]) if f.get("stage_end") else ""}</span>'
                  ) if stage else ""
    block = " is-block" if kind == "route_block" else ""
    return f"""<article class="card{new}{alert}{block}" data-region="{esc(region)}"
      data-tier="{esc(f.get('tier',''))}" data-rel="{rel}" data-new="{1 if f.get('is_new') else 0}"
      data-notify="{esc(f.get('notify','quiet'))}" data-kind="{esc(f.get('kind',''))}">
  <div class="row">
    <span class="score{hi}">{rel}</span>
    <a class="ttl" href="{esc(link)}" target="_blank" rel="noopener">{esc(f.get('title',''))}</a>
  </div>
  <div class="meta">
    {badges}
    <span class="dot" style="background:{color}"></span>{esc(region)} · {esc(f.get('tier',''))}
    <span class="chip lang {langcls}">{esc(LANG_NAMES.get(lang, lang))}</span>
    <span class="chip">{esc(f.get('source_name',''))}</span>{stage_chip}
    <span>· {when}</span>{' <span class="newtag">new</span>' if f.get('is_new') else ''}
  </div>
  <p class="summary">{esc(f.get('summary_en',''))}</p>
  {('<details class="src"><summary>Cached source text</summary><pre>' + esc(f['source_text']) + '</pre></details>') if f.get('source_text') else ''}
</article>"""


def build(findings: list[dict], ledger: dict | None = None,
          conditions: dict | None = None, health: dict | None = None,
          generated: datetime | None = None) -> str:
    """Render the page. `generated` is injectable so the output is testable."""
    # This run's alert-tier finds lead; everything else stays chronological.
    findings = sorted(
        findings,
        key=lambda f: (
            bool(f.get("is_new")) and f.get("notify") == "alert",
            f.get("detected_at", ""),
            f.get("relevance", 0),
        ),
        reverse=True,
    )
    regions = sorted({f.get("region", "whole") for f in findings})
    tiers = sorted({f.get("tier", "other") for f in findings})
    new_count = sum(1 for f in findings if f.get("is_new"))
    alert_count = sum(1 for f in findings
                      if f.get("is_new") and f.get("notify") == "alert")
    now = generated or datetime.now(timezone.utc)
    generated_str = now.isoformat(timespec="minutes").replace("T", " ")
    panel = health_panel(ledger, conditions, health, now)
    cards = "\n".join(card(f) for f in findings) or \
        '<div class="empty"><b>Nothing yet.</b> The first run records a baseline; ' \
        'finds appear from the next run on.</div>'
    region_btns = "".join(
        f'<button class="f" data-k="region" data-v="{esc(r)}">{esc(r)}</button>'
        for r in regions)
    tier_btns = "".join(
        f'<button class="f" data-k="tier" data-v="{esc(t)}">{esc(t)}</button>'
        for t in tiers)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Camino Ignaciano — watch</title>
<style>
:root{{--ink:#1c1a17;--ink-soft:#4a463f;--paper:#f7f4ee;--card:#fffdf9;
--line:#e4ddcf;--waymark:#e8631a;--waymark-deep:#b8470d;--basque:#3f7d57;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
header{{display:flex;align-items:center;gap:13px;padding:24px 30px 14px;
border-bottom:1px solid var(--line);flex-wrap:wrap}}
.sun{{width:30px;height:30px}}
header b{{font-size:16px;letter-spacing:.01em}}
header span.k{{display:block;font-size:10.5px;color:#a89e8c;letter-spacing:.16em;
text-transform:uppercase}}
.gen{{margin-left:auto;font-size:12px;color:var(--ink-soft);text-align:right}}
.gen b{{color:var(--waymark-deep)}}
main{{max-width:900px;margin:0 auto;padding:22px 22px 64px}}
.intro{{color:var(--ink-soft);font-size:13.5px;margin:0 0 18px}}
.filters{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:22px}}
.f{{font:inherit;font-size:12.5px;padding:5px 12px;border:1px solid var(--line);
border-radius:999px;color:var(--ink-soft);background:var(--card);cursor:pointer}}
.f:hover{{border-color:var(--waymark)}}
.f.on{{background:var(--ink);color:#fff;border-color:var(--ink)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:16px 18px 15px;margin-bottom:14px}}
.card.is-new{{border-left:4px solid var(--waymark)}}
.row{{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}}
.score{{font-variant-numeric:tabular-nums;font-weight:700;font-size:13px;color:#fff;
background:var(--ink-soft);border-radius:6px;padding:2px 8px}}
.score.hi{{background:var(--waymark-deep)}}
.ttl{{font-size:17px;font-weight:600;line-height:1.3;color:var(--ink);text-decoration:none}}
.ttl:hover{{color:var(--waymark-deep);text-decoration:underline}}
.meta{{font-size:12px;color:var(--ink-soft);margin:6px 0 0;display:flex;gap:9px;
flex-wrap:wrap;align-items:center}}
.chip{{font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;padding:2px 7px;
border-radius:5px;background:#efe9dd;color:#5b554a}}
.chip.lang.en{{background:#e9efe9;color:#3f7d57}}
.chip.lang.es{{background:#fae9dd;color:var(--waymark-deep)}}
.dot{{width:8px;height:8px;border-radius:50%;display:inline-block}}
.newtag{{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:#fff;
background:var(--waymark);border-radius:5px;padding:2px 6px}}
.summary{{margin:11px 0 0;color:#332f29;font-size:14.5px}}
.empty{{text-align:center;color:var(--ink-soft);padding:60px 20px;
border:1px dashed var(--line);border-radius:14px}}
.empty b{{display:block;font-size:16px;color:var(--ink);margin-bottom:6px}}
.src{{margin-top:10px;border-top:1px solid var(--line);padding-top:8px}}
.src summary{{font-size:12px;color:var(--ink-soft);cursor:pointer;user-select:none}}
.src summary:hover{{color:var(--waymark-deep)}}
.health{{border:1px solid var(--line);background:var(--card);border-radius:12px;
padding:10px 14px;margin:0 0 18px}}
.health>summary{{cursor:pointer;font-size:13px;color:var(--ink-soft);user-select:none}}
.health>summary:hover{{color:var(--waymark-deep)}}
.health b.bad{{color:#b8180d}}
.health b.good{{color:#3f7d57}}
.hbody{{margin-top:10px}}
.hnote{{font-size:12px;color:var(--ink-soft);margin:0 0 10px;line-height:1.5}}
.hbody h4{{font-size:12px;letter-spacing:.08em;text-transform:uppercase;
color:var(--ink-soft);margin:16px 0 6px}}
.htab{{width:100%;border-collapse:collapse;font-size:12px}}
.htab th{{text-align:left;font-weight:600;color:var(--ink-soft);
border-bottom:1px solid var(--line);padding:4px 6px}}
.htab td{{padding:4px 6px;border-bottom:1px solid #f0ece4;vertical-align:top}}
.htab tr.st-failing td{{background:#fff5f3}}
.htab tr.st-stale td{{background:#fdf6ec}}
.pill{{font-size:9.5px;letter-spacing:.07em;font-weight:700;padding:2px 6px;
border-radius:4px;background:#efe9dd;color:#5b554a;white-space:nowrap}}
.pill.failing{{background:#b8180d;color:#fff}}
.pill.stale{{background:#6b2fb5;color:#fff}}
.pill.silent{{background:#e4ecf2;color:#2f6f86}}
.pill.ok{{background:#e9efe9;color:#3f7d57}}
.card.is-alert{{border-left:4px solid #b8180d;background:#fffaf7;
box-shadow:0 1px 0 rgba(184,24,13,.10)}}
.alerttag{{font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:#fff;
background:#b8180d;border-radius:5px;padding:2px 7px;font-weight:700}}
.f.alertf{{border-color:#e0b4ae;color:#8e1a11}}
.f.alertf:hover{{border-color:#b8180d}}
.f.alertf.on{{background:#b8180d;color:#fff;border-color:#b8180d}}
.card.is-block{{border-left:4px solid #6b2fb5}}
.blocktag{{font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:#fff;
background:#6b2fb5;border-radius:5px;padding:2px 7px;font-weight:700}}
.wxtag{{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:#7a4a05;
background:#f7e7c8;border-radius:5px;padding:2px 7px;font-weight:600}}
.f.blockf{{border-color:#cdb6e6;color:#54248f}}
.f.blockf:hover{{border-color:#6b2fb5}}
.f.blockf.on{{background:#6b2fb5;color:#fff;border-color:#6b2fb5}}
.chip.stage{{background:#e4ecf2;color:#2f6f86}}
.gen b.al{{color:#b8180d}}
.src pre{{margin:8px 0 0;font-size:12px;line-height:1.5;white-space:pre-wrap;
word-break:break-word;color:#4a463f;background:#f0ece4;border-radius:7px;
padding:10px 12px;max-height:260px;overflow-y:auto}}
</style></head><body>
<header>
  <svg class="sun" viewBox="0 0 100 100" aria-hidden="true">
    <g fill="none" stroke="#e8631a" stroke-width="6" stroke-linecap="round">
      <circle cx="50" cy="50" r="17" fill="#e8631a" stroke="none"/>
      <line x1="50" y1="6" x2="50" y2="22"/><line x1="50" y1="78" x2="50" y2="94"/>
      <line x1="6" y1="50" x2="22" y2="50"/><line x1="78" y1="50" x2="94" y2="50"/>
      <line x1="19" y1="19" x2="31" y2="31"/><line x1="69" y1="69" x2="81" y2="81"/>
      <line x1="81" y1="19" x2="69" y2="31"/><line x1="31" y1="69" x2="19" y2="81"/>
    </g>
  </svg>
  <div><b>Camino Ignaciano</b><span class="k">watch</span></div>
  <div class="gen">Generated {generated_str} UTC<br><b>{new_count}</b> new this run{f' · <b class="al">{alert_count}</b> alert' if alert_count else ''} · {len(findings)} total</div>
</header>
<main>
  <p class="intro">Loyola → Manresa · 27 stages · 650 km. New and changed content
    across the official site, forums, Spanish &amp; Catalan blogs, town halls and news —
    translated and ranked for a spring walk.</p>
  {panel}
  <div class="filters">
    <button class="f on" data-k="all" data-v="">All</button>
    <button class="f alertf" data-k="notify" data-v="alert">Alerts</button>
    <button class="f" data-k="new" data-v="1">New this run</button>
    <button class="f" data-k="rel" data-v="60">High relevance</button>
    <button class="f blockf" data-k="kind" data-v="route_block">Route blocked</button>
    <button class="f" data-k="kind" data-v="fire">Fire</button>
    {region_btns}{tier_btns}
  </div>
  <div id="list">
    {cards}
  </div>
</main>
<script>
const btns=[...document.querySelectorAll('.f')], cards=[...document.querySelectorAll('.card')];
btns.forEach(b=>b.onclick=()=>{{
  btns.forEach(x=>x.classList.remove('on')); b.classList.add('on');
  const k=b.dataset.k, v=b.dataset.v;
  cards.forEach(c=>{{
    let show=true;
    if(k==='region') show=c.dataset.region===v;
    else if(k==='tier') show=c.dataset.tier===v;
    else if(k==='new') show=c.dataset.new==='1';
    else if(k==='notify') show=c.dataset.notify===v;
    else if(k==='kind') show=(c.dataset.kind||'').startsWith(v);
    else if(k==='rel') show=parseInt(c.dataset.rel)>=parseInt(v);
    c.style.display=show?'':'none';
  }});
}});
</script>
</body></html>"""


def main() -> None:
    def _load(path, default):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return default

    findings = _load(FINDINGS, [])
    out = build(findings, ledger=_load(LEDGER, {}),
                conditions=_load(CONDITIONS, {}), health=_load(HEALTH, {}))
    os.makedirs(os.path.dirname(OUT_DOCS), exist_ok=True)
    for path in (OUT_DOCS, OUT_ROOT):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(out)
    print(f"Wrote report: {len(findings)} finding(s) -> {OUT_DOCS} and {OUT_ROOT}")


if __name__ == "__main__":
    main()
