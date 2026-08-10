#!/usr/bin/env python3
"""Build the m5x2 weekly-reports accountability grid (local static HTML).

One row per week (Sunday-anchored, last N weeks), one column per reporter.
EMAIL is the primary source (per JM 2026-07-26): each cell links to the Gmail
thread of that person's weekly update received that week. The "Weekly Reports"
Drive folder (m5x2 Main) is a secondary source — a Doc dropped there with
`YYYY-MM-DD First-name Topic` naming also fills the cell.

A report lands in the row of the week it was RECEIVED (Monday sends covering
the prior week count for the week they arrive — delivery cadence is what's
being tracked, not coverage windows).

Email matchers (learned from 60d of real sends, 2026-07-26):
  LX        lx@m5c7/m5x2         "m5x2 Weekly Update"
  Ian       ian@m5c7             "Weekly Update — <date>" (delay notices excluded)
  Andie     andrea@m5c7          "Leasing Weekly Update - <range>"
  Stef      stefanie@m5c7        "TRS recap"
  Leeroy    leeroy@ or dulce@    "Turns Weekly Update" / "Turn Operations Update"
  Florencia florencia@m5c7       "Maintenance Service Weekly Update - <range>"
  Lawrence  lawrence@m5c7        "Weekly Projects Update – <date>"

Auth: reuses the workspace-mcp OAuth store (drive + gmail.readonly scopes).
Local page only. Rebuilt every 2h by Straylight cron.

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
DRIVE_ID = "0ALnip0aznECYUk9PVA"                   # m5x2 Main
DEFAULT_WEEKS = 5

# i9 (Xbox) side has no API access (Outlook) — sourced instead by parsing the
# vault doc that /f695 keeps current via manual paste-in. Read-only here;
# this script never writes to it.
F695_DOC = Path.home() / "vault" / "h335" / "f693" / "1-f695-weekly-updates.md"
F695_OBSIDIAN_URI = "obsidian://open?path=" + urllib.parse.quote(str(F695_DOC))
# Fixed roster (the doc's summary tables were removed 2026-08-10; the doc now
# holds only the writeups). Growth (JM) — JM's own weekly — stays rightmost.
I9_PEOPLE = ["Elliot Silvers", "Bei Lu", "Eldon Lei", "Roberto Ruggeri",
             "Jessica Allen", "Ethan Abeles", "Growth (JM)"]
I9_DUE_OFFSET = 5  # i9 updates are due the FRIDAY of their Sunday-anchored week

# (display, topic, drive-name tokens, email senders, subject regex, subject veto regex)
REPORTERS = [
    ("LX", "Portfolio", ("lx", "louisa"),
     ("lx@m5c7.com", "lx@m5x2.com"), r"weekly\s+update", None),
    ("Ian", "Ops", ("ian",),
     ("ian@m5c7.com",), r"^weekly\s+update", r"delay"),
    ("Andie", "Leasing", ("andie", "andrea"),
     ("andrea@m5c7.com",), r"leasing\s+weekly\s+update", None),
    ("Stef", "Tenant Relations", ("stef", "stefanie"),
     ("stefanie@m5c7.com",), r"trs\s+recap", None),
    ("Leeroy", "Turns", ("leeroy", "dulce"),
     ("leeroy@m5c7.com", "dulce@m5c7.com"), r"turns?\s+(operations\s+)?(weekly\s+)?update", None),
    ("Florencia", "R&M", ("florencia", "flo"),
     ("florencia@m5c7.com",), r"(maintenance|service).*weekly\s+update", None),
    ("Lawrence", "Projects", ("lawrence",),
     ("lawrence@m5c7.com",), r"(weekly\s+)?projects\s+update", None),
]
REPLY_PREFIXES = ("re:", "fwd:", "fw:", "accepted:", "declined:", "invitation")


# ---------------------------------------------------------------------------
# Google API plumbing (workspace-mcp token store)
# ---------------------------------------------------------------------------

def access_token() -> str:
    c = json.loads(CREDS.read_text())
    body = urllib.parse.urlencode({
        "client_id": c["client_id"], "client_secret": c["client_secret"],
        "refresh_token": c["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(c["token_uri"], data=body, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]


def _get(tok: str, url: str, params: dict) -> dict:
    q = urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(f"{url}?{q}", headers={"Authorization": f"Bearer {tok}"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def week_start(d: dt.date) -> dt.date:
    """Sunday-anchored week (matches the M.W convention)."""
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


# Gmail-side subject narrowing per reporter (regexes above still decide) —
# one targeted query per reporter keeps this to a handful of metadata fetches
# instead of paging every email seven chatty senders wrote.
SUBJECT_QUERY = {
    "LX": 'subject:"weekly update"', "Ian": 'subject:"weekly update"',
    "Andie": 'subject:"leasing weekly update"', "Stef": "subject:trs",
    "Leeroy": "subject:(turn OR turns)",
    "Florencia": 'subject:"weekly update"', "Lawrence": 'subject:"projects update"',
}


def fetch_emails(tok: str, days: int) -> list[dict]:
    """Original (non-reply) candidate messages per reporter in the window.
    → [{date, sender, subject, thread}]"""
    out, seen = [], set()
    for disp, _t, _dtok, senders, _pat, _veto in REPORTERS:
        q = (f"({' OR '.join('from:' + s for s in senders)}) "
             f"{SUBJECT_QUERY[disp]} newer_than:{days}d")
        res = _get(tok, "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                   {"q": q, "maxResults": 50})
        for m in res.get("messages", []):
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            d = _get(tok, f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m['id']}",
                     {"format": "metadata", "metadataHeaders": ["From", "Subject"]})
            hs = {h["name"]: h["value"] for h in d["payload"]["headers"]}
            subj = (hs.get("Subject") or "").strip()
            if subj.lower().startswith(REPLY_PREFIXES):
                continue
            out.append({
                "date": dt.datetime.fromtimestamp(int(d["internalDate"]) / 1000).date(),
                "sender": (hs.get("From") or "").split("<")[-1].rstrip(">").lower(),
                "subject": subj,
                "thread": d.get("threadId", m["id"]),
            })
    return out


def fetch_drive_docs(tok: str) -> list[dict]:
    files, page = [], None
    while True:
        params = {
            "q": f"'{FOLDER_ID}' in parents and trashed = false",
            "fields": "nextPageToken,files(id,name,createdTime,webViewLink)",
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
            "corpora": "drive", "driveId": DRIVE_ID, "pageSize": "200",
            **({"pageToken": page} if page else {}),
        }
        d = _get(tok, "https://www.googleapis.com/drive/v3/files", params)
        files += d.get("files", [])
        page = d.get("nextPageToken")
        if not page:
            return files


def doc_date(f: dict) -> dt.date:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", f["name"])
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return dt.datetime.fromisoformat(f["createdTime"].replace("Z", "+00:00")).date()


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------

def build_grid(emails: list[dict], docs: list[dict]) -> dict:
    """{(week_start, display): [{url, title, kind}]}"""
    grid: dict = {}
    for e in emails:
        for disp, _t, _dtok, senders, pat, veto in REPORTERS:
            if e["sender"] in senders and re.search(pat, e["subject"], re.I) \
                    and not (veto and re.search(veto, e["subject"], re.I)):
                grid.setdefault((week_start(e["date"]), disp), []).append({
                    "url": f"https://mail.google.com/mail/u/0/#all/{e['thread']}",
                    "title": f"{e['date'].isoformat()} · {e['subject']}", "kind": "mail"})
                break
    for f in docs:
        name_l = f["name"].lower()
        for disp, _t, dtok, *_ in REPORTERS:
            if any(re.search(r"\b" + re.escape(t) + r"\b", name_l) for t in dtok):
                grid.setdefault((week_start(doc_date(f)), disp), []).append({
                    "url": f["webViewLink"], "title": f["name"], "kind": "doc"})
                break
    return grid


# ---------------------------------------------------------------------------
# i9 (Xbox) side — parsed from the /f695 vault doc, not an API
# ---------------------------------------------------------------------------

def _parse_week_heading(line: str) -> dt.date | None:
    m = re.match(r"^### Week of (\d{4})\.(\d{2})\.(\d{2})", line)
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def fetch_i9_grid() -> dict:
    """grid[(week_start, person)] = {"grade": "C"|"", "late": bool} for each
    #### <Person> entry under a ### Week of heading. The grade comes from the
    entry's closing **JM (<grade>):** line; a "#### <Person> (late)" heading
    marks an update that arrived after its Friday due date."""
    if not F695_DOC.exists():
        return {}
    grid: dict = {}
    in_log = False
    cur_week: dt.date | None = None
    cur_person: str | None = None
    for line in F695_DOC.read_text(encoding="utf-8").splitlines():
        if line.strip() == "## Weekly updates":
            in_log = True
            continue
        if not in_log:
            continue
        if line.startswith("## "):
            break  # left the Weekly updates section
        wk = _parse_week_heading(line)
        if wk is not None:
            cur_week = wk
            cur_person = None
            continue
        m = re.match(r"^#### (.+?)(\s*\(late\))?$", line.strip())
        if m and line.strip().startswith("#### ") and cur_week is not None:
            cur_person = m.group(1).strip()
            grid[(cur_week, cur_person)] = {"grade": "", "late": bool(m.group(2))}
            continue
        g = re.match(r"^\*\*JM \(([A-Fa-f][+-]?)\):", line.strip())
        if g and cur_week is not None and cur_person is not None:
            grid[(cur_week, cur_person)]["grade"] = g.group(1).upper()
    return grid


def jm_draft_in_progress(grid: dict) -> tuple[dt.date, str] | None:
    """JM's own weekly, mid-draft: newest *growth-weekly-jm-draft*.md with
    frontmatter `status: draft`. Returns (covered_week, obsidian_uri); the
    covered week is read from the first M/D in the H1 title (e.g. "Mon 8/3
    to Fri 8/7"), falling back to the week before the draft's date."""
    for f in sorted(F695_DOC.parent.glob("*growth-weekly-jm-draft*.md"), reverse=True):
        text = f.read_text(encoding="utf-8")
        if not re.search(r"^status:\s*draft\s*$", text, re.M):
            continue
        ym = re.search(r"^date:\s*(\d{4})", text, re.M)
        year = int(ym.group(1)) if ym else dt.date.today().year
        m = re.search(r"^# .*?(\d{1,2})/(\d{1,2})", text, re.M)
        if m:
            try:
                wk = week_start(dt.date(year, int(m.group(1)), int(m.group(2))))
            except ValueError:
                wk = week_start(dt.date.today()) - dt.timedelta(weeks=1)
        else:
            wk = week_start(dt.date.today()) - dt.timedelta(weeks=1)
        if grid.get((wk, "Growth (JM)")) is not None:
            return None  # already filed — the ✓ wins
        return wk, "obsidian://open?path=" + urllib.parse.quote(str(f))
    return None


def render_i9_section(grid: dict, weeks: list[dt.date]) -> str:
    today = dt.date.today()
    cur_wk = week_start(today)
    draft = jm_draft_in_progress(grid)
    head = "".join(f"<th>{html.escape(p)}</th>" for p in I9_PEOPLE)
    rows = []
    for wk in weeks:
        cells = []
        due_day = wk + dt.timedelta(days=I9_DUE_OFFSET)
        for p in I9_PEOPLE:
            hit = grid.get((wk, p))
            if hit is not None:
                grade = f" <span class='grade'>{html.escape(hit['grade'])}</span>" if hit["grade"] else ""
                late = " <span title='arrived after the Friday due date'>⚠️</span>" if hit["late"] else ""
                cells.append(
                    f"<td class='ok'><a href='{html.escape(F695_OBSIDIAN_URI)}' "
                    f"title='{html.escape(p)} — logged in f695 for week of {wk.isoformat()}'>✓</a>{grade}{late}</td>")
            elif p == "Growth (JM)" and draft and draft[0] == wk:
                cells.append(
                    f"<td class='wip'><a href='{html.escape(draft[1])}' "
                    f"title='JM draft in progress — click to open'>✍️</a></td>")
            elif wk == cur_wk:
                cells.append(f"<td class='due'>due {due_day.month}/{due_day.day}</td>")
            else:
                cells.append("<td class='miss'>—</td>")
        label = f"{wk.month}/{wk.day}" + (" (this wk)" if wk == cur_wk else "")
        rows.append(f"<tr><td class='wk'>{label}</td>{''.join(cells)}</tr>")
    return f"""
<h1 style="margin-top:32px">i9 (Xbox) Weekly Reports</h1>
<div class="sub">Outlook has no API access — filed manually via <code>/f695</code> into
<a class="folder" href="{html.escape(F695_OBSIDIAN_URI)}">1-f695-weekly-updates.md</a></div>
<table><tr><th style="text-align:left">Week of</th>{head}</tr>
{''.join(rows)}
</table>
<div class="note">✓ opens the f695 doc (hover for which week/person); a letter next to it
is JM's grade for that update; ⚠️ = arrived after its Friday due date. An update counts
for the week it COVERS (a Friday send covers its own week) and is due the Friday of the
current week. ✍️ = JM's own update mid-draft (click to open the draft); — is a week with
nothing logged.</div>
"""


def render(grid: dict, weeks: list[dt.date], n_mail: int, n_docs: int, i9_html: str = "") -> str:
    today = dt.date.today()
    cur_wk = week_start(today)
    head = "".join(f"<th>{html.escape(d)}<span class='topic'>{html.escape(t)}</span></th>"
                   for d, t, *_ in REPORTERS)
    rows = []
    for wk in weeks:
        cells = []
        for disp, *_ in REPORTERS:
            hits = grid.get((wk, disp), [])
            if hits:
                links = " ".join(
                    f"<a href='{html.escape(h['url'])}' title='{html.escape(h['title'])}'>"
                    f"{'✓' if h['kind'] == 'mail' else '📄'}</a>" for h in hits)
                cells.append(f"<td class='ok'>{links}</td>")
            elif wk == cur_wk:
                cells.append("<td class='due'>due</td>")
            else:
                cells.append("<td class='miss'>—</td>")
        label = f"{wk.month}/{wk.day}" + (" (this wk)" if wk == cur_wk else "")
        rows.append(f"<tr><td class='wk'>{label}</td>{''.join(cells)}</tr>")
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
td.due{{color:#ff8a3d;font-size:12px;white-space:nowrap}}
td.wip a{{text-decoration:none;font-size:15px}}
td.ok .grade{{color:#8b96a3;font-weight:700;font-size:12px}}
.note{{color:#8b96a3;font-size:12px;margin-top:12px}}
a.folder{{color:#2979ff}}
</style></head><body>
<h1>m5x2 Weekly Reports</h1>
<div class="sub">As of {today.isoformat()} · {n_mail} update emails + {n_docs} docs in
<a class="folder" href="{FOLDER_URL}">Weekly Reports</a> scanned</div>
<table><tr><th style="text-align:left">Week of</th>{head}</tr>
{''.join(rows)}
</table>
<div class="note">✓ links to the Gmail thread of that week's update email (hover for date + subject);
📄 links to a Doc in the <a class="folder" href="{FOLDER_URL}">Weekly Reports folder</a>
(named <b>YYYY-MM-DD First-name Topic</b>). A report counts for the Sunday-anchored week it was
received. — is a missed week; the current week shows "due" until it arrives.</div>
{i9_html}
</body></html>"""


def main() -> int:
    n = DEFAULT_WEEKS
    if "--weeks" in sys.argv:
        n = int(sys.argv[sys.argv.index("--weeks") + 1])
    cur = week_start(dt.date.today())
    weeks = [cur - dt.timedelta(weeks=i) for i in range(n)]
    tok = access_token()
    emails = fetch_emails(tok, days=n * 7 + 7)
    docs = fetch_drive_docs(tok)
    grid = build_grid(emails, docs)
    n_mail = sum(1 for v in grid.values() for h in v if h["kind"] == "mail")
    i9_grid = fetch_i9_grid()
    i9_html = render_i9_section(i9_grid, weeks)
    out = DIR / "index.html"
    out.write_text(render(grid, weeks, n_mail, len(docs), i9_html))
    print(f"wrote {out} ({len(emails)} originals scanned, {n_mail} update emails matched, "
          f"{len(docs)} docs, {len(i9_grid)} i9 entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
