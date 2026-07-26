#!/usr/bin/env python3
"""Build the m5x2 weekly-reports accountability grid (local static HTML).

One row per week (Sunday-anchored, last N weeks), one column per reporter.
A cell links to that person's report Doc for that week if one exists in the
"Weekly Reports" folder of the m5x2 Main shared drive; otherwise it shows
missing (red) — or "due" (amber) for the current, still-open week.

Convention the team follows: drop ONE Google Doc per person per week into the
folder, named  `YYYY-MM-DD <First name> <Topic>`  (e.g. "2026-07-26 Ian Ops").
Matching is forgiving: any YYYY-MM-DD or M/D date anywhere in the name buckets
the doc into that date's week (fallback: the doc's createdTime); the reporter
is matched by first name, case-insensitive. One doc can satisfy only its own
week — no doc, no checkmark.

Auth: reuses the workspace-mcp OAuth store
(~/.google_workspace_mcp/credentials/mckay@m5c7.com.json — full drive scope,
refresh token) so no separate credential setup. Local page only, not
published anywhere (per JM 2026-07-26).

Usage: build.py [--weeks N]   (default 5; writes index.html next to this file)
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DIR = Path(__file__).resolve().parent
CREDS = Path.home() / ".google_workspace_mcp/credentials/mckay@m5c7.com.json"
FOLDER_ID = "1UoUd5ql-CIAGaEAR_V8cY_idtIAj28MQ"   # m5x2 Main → "Weekly Reports"
FOLDER_URL = f"https://drive.google.com/drive/folders/{FOLDER_ID}"
DEFAULT_WEEKS = 5

# (display name, topic, name tokens that identify the reporter in a filename)
REPORTERS = [
    ("LX",        "Portfolio",        ("lx", "louisa")),
    ("Ian",       "Ops",              ("ian",)),
    ("Andie",     "Leasing",          ("andie",)),
    ("Stef",      "Tenant Relations", ("stef", "stefanie")),
    ("Leeroy",    "Turns",            ("leeroy",)),
    ("Florencia", "R&M",              ("florencia", "flo")),
    ("Lawrence",  "Projects",         ("lawrence",)),
]


def access_token() -> str:
    c = json.loads(CREDS.read_text())
    body = urllib.parse.urlencode({
        "client_id": c["client_id"], "client_secret": c["client_secret"],
        "refresh_token": c["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(c["token_uri"], data=body, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]


def list_folder(tok: str) -> list[dict]:
    files, page = [], None
    while True:
        q = urllib.parse.urlencode({
            "q": f"'{FOLDER_ID}' in parents and trashed = false",
            "fields": "nextPageToken,files(id,name,createdTime,modifiedTime,webViewLink,mimeType)",
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
            "corpora": "drive", "driveId": "0ALnip0aznECYUk9PVA",
            "pageSize": "200", **({"pageToken": page} if page else {}),
        })
        req = urllib.request.Request(f"https://www.googleapis.com/drive/v3/files?{q}",
                                     headers={"Authorization": f"Bearer {tok}"})
        d = json.loads(urllib.request.urlopen(req, timeout=30).read())
        files += d.get("files", [])
        page = d.get("nextPageToken")
        if not page:
            return files


def week_start(d: dt.date) -> dt.date:
    """Sunday-anchored week (matches the M.W convention)."""
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def doc_date(f: dict) -> dt.date:
    """Date a doc belongs to: from its name if present, else createdTime."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", f["name"])
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})[/.](\d{1,2})\b", f["name"])
    if m:
        today = dt.date.today()
        try:
            d = dt.date(today.year, int(m.group(1)), int(m.group(2)))
            return d if d <= today + dt.timedelta(days=7) else d.replace(year=today.year - 1)
        except ValueError:
            pass
    return dt.datetime.fromisoformat(f["createdTime"].replace("Z", "+00:00")).date()


def build_grid(files: list[dict], weeks: list[dt.date]) -> dict:
    """{(week_start, reporter_display): [docs]}"""
    grid: dict = {}
    for f in files:
        wk = week_start(doc_date(f))
        name_l = f["name"].lower()
        for disp, _topic, tokens in REPORTERS:
            if any(re.search(r"\b" + re.escape(t) + r"\b", name_l) for t in tokens):
                grid.setdefault((wk, disp), []).append(f)
                break
    return grid


def render(grid: dict, weeks: list[dt.date], n_files: int) -> str:
    today = dt.date.today()
    cur_wk = week_start(today)
    head = "".join(f"<th>{html.escape(d)}<span class='topic'>{html.escape(t)}</span></th>"
                   for d, t, _ in REPORTERS)
    rows = []
    for wk in weeks:
        cells = []
        for disp, _t, _tok in REPORTERS:
            docs = grid.get((wk, disp), [])
            if docs:
                links = " ".join(
                    f"<a href='{html.escape(f['webViewLink'])}' title='{html.escape(f['name'])}'>✓</a>"
                    for f in docs)
                cells.append(f"<td class='ok'>{links}</td>")
            elif wk == cur_wk:
                cells.append("<td class='due'>due</td>")
            else:
                cells.append("<td class='miss'>—</td>")
        label = f"{wk.month}/{wk.day}" + (" (this wk)" if wk == cur_wk else "")
        rows.append(f"<tr><td class='wk'>{label}</td>{cells and ''.join(cells)}</tr>")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>m5x2 Weekly Reports</title>
<style>
body{{margin:0;background:#0d0f12;color:#e7ecf0;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;padding:24px;max-width:1000px;margin:auto}}
h1{{font-size:22px;margin:0 0 2px}}
.sub{{color:#8b96a3;font-size:12px;margin-bottom:18px}}
table{{border-collapse:collapse;width:100%;background:#161a1f;border:1px solid #242a31;border-radius:8px}}
th,td{{padding:10px 12px;text-align:center;border-bottom:1px solid #242a31}}
th{{color:#e7ecf0;font-size:13px}} th .topic{{display:block;color:#8b96a3;font-weight:400;font-size:11px}}
td.wk{{color:#8b96a3;font-variant-numeric:tabular-nums;text-align:left;white-space:nowrap}}
td.ok a{{color:#2faa4d;font-weight:700;text-decoration:none;font-size:16px}}
td.miss{{color:#e23b3b;font-weight:700}}
td.due{{color:#ff8a3d;font-size:12px}}
.note{{color:#8b96a3;font-size:12px;margin-top:12px}}
a.folder{{color:#2979ff}}
</style></head><body>
<h1>m5x2 Weekly Reports</h1>
<div class="sub">As of {today.isoformat()} · scanning <a class="folder" href="{FOLDER_URL}">m5x2 Main / Weekly Reports</a> · {n_files} docs found</div>
<table><tr><th style="text-align:left">Week of</th>{head}</tr>
{''.join(rows)}
</table>
<div class="note">Convention: one Google Doc per person per week in the folder, named
<b>YYYY-MM-DD First-name Topic</b> (e.g. "2026-07-26 Ian Ops"). A doc is bucketed into the
Sunday-anchored week of the date in its name (falls back to its creation date). ✓ links to the doc;
— is a missed week; the current week shows "due" until filed.</div>
</body></html>"""


def main() -> int:
    n = DEFAULT_WEEKS
    if "--weeks" in sys.argv:
        n = int(sys.argv[sys.argv.index("--weeks") + 1])
    cur = week_start(dt.date.today())
    weeks = [cur - dt.timedelta(weeks=i) for i in range(n)]
    tok = access_token()
    files = list_folder(tok)
    grid = build_grid(files, weeks)
    out = DIR / "index.html"
    out.write_text(render(grid, weeks, len(files)))
    print(f"wrote {out} ({len(files)} docs, {n} weeks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
