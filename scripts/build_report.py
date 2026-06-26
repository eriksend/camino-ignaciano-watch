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
    return f"""<article class="card{new}" data-region="{esc(region)}"
      data-tier="{esc(f.get('tier',''))}" data-rel="{rel}" data-new="{1 if f.get('is_new') else 0}">
  <div class="row">
    <span class="score{hi}">{rel}</span>
    <a class="ttl" href="{esc(link)}" target="_blank" rel="noopener">{esc(f.get('title',''))}</a>
  </div>
  <div class="meta">
    <span class="dot" style="background:{color}"></span>{esc(region)} · {esc(f.get('tier',''))}
    <span class="chip lang {langcls}">{esc(LANG_NAMES.get(lang, lang))}</span>
    <span class="chip">{esc(f.get('source_name',''))}</span>
    <span>· {when}</span>{' <span class="newtag">new</span>' if f.get('is_new') else ''}
  </div>
  <p class="summary">{esc(f.get('summary_en',''))}</p>
  {('<details class="src"><summary>Cached source text</summary><pre>' + esc(f['source_text']) + '</pre></details>') if f.get('source_text') else ''}
</article>"""


def build(findings: list[dict]) -> str:
    findings = sorted(
        findings,
        key=lambda f: (f.get("detected_at", ""), f.get("relevance", 0)),
        reverse=True,
    )
    regions = sorted({f.get("region", "whole") for f in findings})
    tiers = sorted({f.get("tier", "other") for f in findings})
    new_count = sum(1 for f in findings if f.get("is_new"))
    generated = datetime.now(timezone.utc).isoformat(timespec="minutes").replace("T", " ")
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
  <div class="gen">Generated {generated} UTC<br><b>{new_count}</b> new this run · {len(findings)} total</div>
</header>
<main>
  <p class="intro">Loyola → Manresa · 27 stages · 650 km. New and changed content
    across the official site, forums, Spanish &amp; Catalan blogs, town halls and news —
    translated and ranked for a spring walk.</p>
  <div class="filters">
    <button class="f on" data-k="all" data-v="">All</button>
    <button class="f" data-k="new" data-v="1">New this run</button>
    <button class="f" data-k="rel" data-v="60">High relevance</button>
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
    else if(k==='rel') show=parseInt(c.dataset.rel)>=parseInt(v);
    c.style.display=show?'':'none';
  }});
}});
</script>
</body></html>"""


def main() -> None:
    try:
        with open(FINDINGS, encoding="utf-8") as fh:
            findings = json.load(fh)
    except Exception:
        findings = []
    out = build(findings)
    os.makedirs(os.path.dirname(OUT_DOCS), exist_ok=True)
    for path in (OUT_DOCS, OUT_ROOT):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(out)
    print(f"Wrote report: {len(findings)} finding(s) -> {OUT_DOCS} and {OUT_ROOT}")


if __name__ == "__main__":
    main()
