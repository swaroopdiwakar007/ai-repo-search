#!/usr/bin/env python3
"""Build a self-contained, offline HTML dashboard from repo_scraper/seen_repos.json
and repo_tracker.csv.

Usage: python tools/html_report.py [--out reports/repo_report.html] [--top 25]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import repo_state  # noqa: E402

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Similar Repo Report</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--mut:#6b6b66;--line:#e4e3de;--acc:#3b6fd8;--warn:#c4682b;--bad:#b23a3a;--ok:#2f8a57}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#20201e;--ink:#ecebe6;--mut:#9b9a93;--line:#34332f;--acc:#7aa2f0;--warn:#e09452;--bad:#e06a6a;--ok:#5cc28a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}.sub{color:var(--mut);margin:0 0 20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}.card b{display:block;font-size:24px}.card span{color:var(--mut);font-size:12px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:20px;overflow-x:auto}
h2{font-size:15px;margin:0 0 12px}svg text{fill:var(--ink);font-size:11px}
input,select{background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 8px;margin:0 8px 10px 0}
table{border-collapse:collapse;width:100%;min-width:760px}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:600;font-size:12px;cursor:pointer}td.n{text-align:right;font-variant-numeric:tabular-nums}
a{color:var(--acc);text-decoration:none}.pill{font-size:11px;padding:1px 7px;border-radius:9px;border:1px solid var(--line);white-space:nowrap}
.FAIL{color:var(--bad)}.FLAG{color:var(--warn)}.desc{color:var(--mut);font-size:12px;max-width:380px}
</style></head><body><main>
<h1>Similar repository report</h1><p class="sub">Seeds: __SEEDS__</p>
<div class="cards">__CARDS__</div>
<section><h2>Top repositories by total score</h2>__CHART__</section>
<section><h2>All scored repositories</h2>
<input id="q" placeholder="Filter by name, language, topic"><select id="st"><option value="">All statuses</option></select>
<table id="t"><thead><tr><th data-k="full_name">Repository</th><th data-k="total">Total</th><th data-k="purpose">Purpose</th><th data-k="features">Features</th><th data-k="stack">Stack</th><th data-k="docs">Docs</th><th data-k="health">Health</th><th data-k="stars">Stars</th><th data-k="status">Status</th></tr></thead><tbody></tbody></table>
</section></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const rows=JSON.parse(document.getElementById('data').textContent);
const tb=document.querySelector('#t tbody'),q=document.getElementById('q'),st=document.getElementById('st');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
[...new Set(rows.map(r=>r.status))].sort().forEach(s=>{const o=document.createElement('option');o.value=o.textContent=s;st.appendChild(o)});
let sortK='total',dir=-1;
function render(){const f=q.value.toLowerCase();
 const list=rows.filter(r=>(!st.value||r.status===st.value)&&(!f||(r.full_name+' '+(r.language||'')+' '+(r.topics||[]).join(' ')+' '+(r.description||'')).toLowerCase().includes(f)))
  .sort((a,b)=>{const x=a[sortK]??-1,y=b[sortK]??-1;return (x>y?1:x<y?-1:0)*dir});
 tb.innerHTML=list.map(r=>`<tr><td><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.full_name)}</a>
 <div class="desc">${esc(r.description)}</div>${r.dealbreaker&&r.dealbreaker!=='PASS'?`<div class="${esc(r.dealbreaker)}">${esc(r.dealbreaker)}: ${esc(r.dealbreaker_note)}</div>`:''}</td>
 <td class="n"><b>${esc(r.total??'')}</b></td><td class="n">${esc(r.purpose??'')}</td><td class="n">${esc(r.features??'')}</td><td class="n">${esc(r.stack??'')}</td>
 <td class="n">${esc(r.docs??'')}</td><td class="n">${esc(r.health)}</td><td class="n">${esc(r.stars)}</td>
 <td><span class="pill">${esc(r.status)}</span>${r.tracked?` <span class="pill">${esc(r.tracked)}</span>`:''}</td></tr>`).join('');}
document.querySelectorAll('th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;dir=sortK===k?-dir:-1;sortK=k;render()});
q.oninput=st.onchange=render;render();
</script></body></html>"""


def chart(rows: list[dict]) -> str:
    if not rows:
        return "<p class='sub'>No ranked repositories yet. Run /rank.</p>"
    bar_h, gap, label_w, w = 18, 6, 260, 760
    h = len(rows) * (bar_h + gap)
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Top repositories by score">']
    for i, r in enumerate(rows):
        y = i * (bar_h + gap)
        bw = (w - label_w - 50) * (r["total"] or 0) / 100
        out.append(f'<text x="0" y="{y + 13}">{html.escape(r["full_name"][:38])}</text>')
        out.append(f'<rect x="{label_w}" y="{y}" width="{bw:.1f}" height="{bar_h}" rx="3" fill="var(--acc)"/>')
        out.append(f'<text x="{label_w + bw + 6:.1f}" y="{y + 13}">{r["total"]}</text>')
    out.append("</svg>")
    return "".join(out)


def build(top: int) -> str:
    st = repo_state.load()
    tracked = repo_state.tracked_keys()
    rows = []
    for k, r in st["repos"].items():
        s = r.get("scores") or {}
        rows.append({"full_name": r.get("full_name", k), "url": r.get("url"), "description": r.get("description"),
                     "language": r.get("language"), "topics": r.get("topics"), "stars": r.get("stars"),
                     "health": r.get("health"), "total": r.get("total"), "status": r.get("status"),
                     "dealbreaker": r.get("dealbreaker"), "dealbreaker_note": r.get("dealbreaker_note"),
                     "tracked": tracked.get(k), **{d: s.get(d) for d in ("purpose", "features", "stack", "docs")}})
    ranked = sorted([r for r in rows if r["status"] == "ranked"], key=lambda r: r["total"] or 0, reverse=True)
    by = lambda s: sum(1 for r in rows if r["status"] == s)  # noqa: E731
    cards = [("Discovered", len(rows)), ("Ranked", by("ranked")), ("Vetoed", by("vetoed")),
             ("Awaiting rank", by("new") + by("unreachable")), ("Tracked", len(tracked)),
             ("Best score", ranked[0]["total"] if ranked else "-")]
    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    seeds = ", ".join(html.escape(v.get("full_name") or k) for k, v in st["seeds"].items()) or "none (query search only)"
    return (TEMPLATE.replace("__SEEDS__", seeds)
            .replace("__CARDS__", "".join(f"<div class='card'><b>{v}</b><span>{n}</span></div>" for n, v in cards))
            .replace("__CHART__", chart(ranked[:top]))
            .replace("__DATA__", data))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=os.path.join(repo_state.ROOT, "reports", "repo_report.html"))
    p.add_argument("--top", type=int, default=20)
    a = p.parse_args(argv)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(build(a.top))
    print(os.path.abspath(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
