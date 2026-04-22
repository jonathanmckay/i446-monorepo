#!/usr/bin/env python3
"""Q2 m5x2 Goals dashboard. Reads goals.json, renders a grouped goal table."""

import json
from pathlib import Path
from flask import Flask

ROOT = Path(__file__).parent
GOALS_PATH = ROOT / "goals.json"

app = Flask(__name__)


def load_goals():
    return json.loads(GOALS_PATH.read_text())


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


def render():
    data = load_goals()
    quarter = data.get("quarter", "")
    sections_html = []
    for section in data.get("sections", []):
        rows = []
        for g in section["goals"]:
            p = g.get("p", "")
            link = g.get("link")
            link_cell = f'<a href="{link}" target="_blank">open →</a>' if link else '<span class="na">—</span>'
            rows.append(
                f'<tr>'
                f'<td class="p p-{p}">{p}</td>'
                f'<td class="dri">{g.get("dri","")}</td>'
                f'<td class="goal">{g.get("goal","")}</td>'
                f'<td class="actual">{g.get("q1_actual","")}</td>'
                f'<td class="link">{link_cell}</td>'
                f'</tr>'
            )
        sections_html.append(
            f'<div class="section">'
            f'<h2>{section["name"]}</h2>'
            f'<table>'
            f'<colgroup><col class="c-p"><col class="c-dri"><col class="c-goal"><col class="c-actual"><col class="c-link"></colgroup>'
            f'<thead><tr><th>P</th><th>DRI</th><th>Goal</th><th>Q1 Actual</th><th>Link</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody>'
            f'</table>'
            f'</div>'
        )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Q2 m5x2 Goals</title>
{STYLE}
</head>
<body>
<div class="topbar">
  <h1>Q2 m5x2 GOALS</h1>
  <span class="quarter">{quarter}</span>
</div>
{"".join(sections_html)}
</body>
</html>
"""


@app.route("/")
@app.route("/goals/")
def index():
    return render()


@app.route("/api/goals")
@app.route("/goals/api/goals")
def api_goals():
    return load_goals()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5559, debug=False)
