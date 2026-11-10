#!/usr/bin/env python3
"""m5x2 Goals dashboard. Reads goals.json (current quarter) or goals-<YYYYqN>.json
(archived quarter, via ?q=2026q2) and renders a grouped goal table.

Goal rows carry p, dri, goal, link plus either the legacy q1_actual field or the
roadmap fields metric, baseline, target, due, source.
"""

import json
import re
from html import escape
from pathlib import Path
from flask import Flask, abort, request

ROOT = Path(__file__).parent
GOALS_PATH = ROOT / "goals.json"

app = Flask(__name__)


QUARTER_RE = re.compile(r"^\d{4}q[1-4]$")


def goals_path(q=None):
    if not q:
        return GOALS_PATH
    if not QUARTER_RE.match(q):
        return None
    path = ROOT / f"goals-{q}.json"
    return path if path.exists() else None


def load_goals(q=None):
    path = goals_path(q)
    if path is None:
        abort(404)
    return json.loads(path.read_text())


def _e(value):
    return escape("" if value is None else str(value))


STYLE = """
<style>
:root {
  --bg: #111; --card: #1a1a1a; --text: #eee;
  --h1: #aaa; --h2: #666; --row-alt: #141414;
  --border: #222; --muted: #666;
  --p1: #d50032; --p2: #f0a500; --p3: #888;
  --link: #4ea3ff;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f4f4f4; --card: #fff; --text: #111;
    --h1: #444; --h2: #888; --row-alt: #fafafa;
    --border: #e0e0e0; --muted: #888;
    --p1: #d50032; --p2: #d97706; --p3: #999;
    --link: #1d6fe0;
  }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: 'SF Mono', ui-monospace, monospace; padding: 24px; }
.topbar { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 24px; flex-wrap: wrap; }
h1 { font-size: 18px; color: var(--h1); letter-spacing: 2px; }
.quarter { font-size: 12px; color: var(--muted); letter-spacing: 1px; }
.section { background: var(--card); border-radius: 8px; padding: 16px 20px; margin-bottom: 20px; }
.section h2 { font-size: 13px; color: var(--h2); margin-bottom: 12px; letter-spacing: 1px; text-transform: uppercase; }
table { width: 100%; border-collapse: collapse; font-size: 13px; table-layout: fixed; }
col.c-p { width: 48px; }
col.c-dri { width: 96px; }
col.c-goal { width: auto; }
col.c-actual { width: 120px; }
col.c-link { width: 72px; }
col.c-base, col.c-target { width: 18%; }
col.c-due { width: 92px; }
col.c-src { width: 14%; }
.metric { color: var(--muted); font-size: 11px; margin-top: 3px; }
.due, .src { color: var(--muted); font-size: 11px; }
thead th { text-align: left; font-weight: normal; color: var(--muted); text-transform: uppercase; font-size: 10px; letter-spacing: 1px; padding: 6px 8px; border-bottom: 1px solid var(--border); }
tbody td { padding: 8px; border-bottom: 1px solid var(--border); vertical-align: top; overflow-wrap: anywhere; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:nth-child(even) { background: var(--row-alt); }
.p { font-weight: bold; letter-spacing: 1px; }
.p-P1 { color: var(--p1); }
.p-P2 { color: var(--p2); }
.p-P3 { color: var(--p3); }
.dri { color: var(--muted); white-space: nowrap; }
.actual { text-align: left; font-variant-numeric: tabular-nums; }
.link a { color: var(--link); text-decoration: none; }
.link a:hover { text-decoration: underline; }
.link .na { color: var(--muted); opacity: 0.4; }
@media (max-width: 640px) {
  body { padding: 12px; }
  table { font-size: 12px; }
  tbody td { padding: 6px; }
}
</style>
"""


LEGACY_COLS = [("P", "c-p"), ("DRI", "c-dri"), ("Goal", "c-goal"), ("Q1 Actual", "c-actual"), ("Link", "c-link")]
ROADMAP_COLS = [("P", "c-p"), ("DRI", "c-dri"), ("Goal", "c-goal"), ("Baseline", "c-base"),
                ("Target", "c-target"), ("Due", "c-due"), ("Source", "c-src")]


def _is_roadmap(data):
    return any("target" in g for s in data.get("sections", []) for g in s.get("goals", []))


def _row(g, roadmap):
    p = _e(g.get("p", ""))
    cells = [f'<td class="p p-{p}">{p}</td>', f'<td class="dri">{_e(g.get("dri"))}</td>']
    if roadmap:
        goal = _e(g.get("goal"))
        if g.get("metric"):
            goal += f'<div class="metric">{_e(g["metric"])}</div>'
        if g.get("link"):
            goal += f' <a href="{_e(g["link"])}" target="_blank">open →</a>'
        cells += [f'<td class="goal">{goal}</td>',
                  f'<td class="actual">{_e(g.get("baseline"))}</td>',
                  f'<td class="actual">{_e(g.get("target"))}</td>',
                  f'<td class="due">{_e(g.get("due"))}</td>',
                  f'<td class="src">{_e(g.get("source"))}</td>']
    else:
        link = g.get("link")
        link_cell = f'<a href="{_e(link)}" target="_blank">open →</a>' if link else '<span class="na">—</span>'
        cells += [f'<td class="goal">{_e(g.get("goal"))}</td>',
                  f'<td class="actual">{_e(g.get("q1_actual"))}</td>',
                  f'<td class="link">{link_cell}</td>']
    return f'<tr>{"".join(cells)}</tr>'


def render(q=None):
    data = load_goals(q)
    roadmap = _is_roadmap(data)
    cols = ROADMAP_COLS if roadmap else LEGACY_COLS
    title = _e(data.get("title") or "m5x2 GOALS")
    sub = _e(data.get("quarter", ""))
    if data.get("as_of"):
        sub += f" · as of {_e(data['as_of'])}"
    colgroup = "".join(f'<col class="{c}">' for _, c in cols)
    thead = "".join(f"<th>{h}</th>" for h, _ in cols)
    sections_html = []
    for section in data.get("sections", []):
        # P1 before P2 within a section (JM 2026-11-10); stable, so json order breaks ties.
        goals = sorted(section.get("goals", []), key=lambda g: str(g.get("p", "P9")))
        rows = "".join(_row(g, roadmap) for g in goals)
        sections_html.append(
            f'<div class="section">'
            f'<h2>{_e(section.get("name"))}</h2>'
            f'<table>'
            f'<colgroup>{colgroup}</colgroup>'
            f'<thead><tr>{thead}</tr></thead>'
            f'<tbody>{rows}</tbody>'
            f'</table>'
            f'</div>'
        )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{STYLE}
</head>
<body>
<div class="topbar">
  <h1>{title}</h1>
  <span class="quarter">{sub}</span>
</div>
{"".join(sections_html)}
</body>
</html>
"""


@app.route("/")
@app.route("/goals/")
def index():
    return render(request.args.get("q"))


@app.route("/api/goals")
@app.route("/goals/api/goals")
def api_goals():
    return load_goals(request.args.get("q"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5559, debug=False)  # LAN + Tailscale, like :5558 (JM 2026-11-10: unreachable from Straylight)
