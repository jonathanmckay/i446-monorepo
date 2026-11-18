#!/usr/bin/env python3
"""2neon.py — the 2₦ monthly checklist (Neon 1n+ rows 62-87) and its Todoist cards.

The block is the definition: A = due day of month, B = name, C = expected
minutes, D = 分. Each row owns one recurring Todoist card, label 5^2₦, named
as col B (or lib/neon/monthly.MONTHLY_ALIASES), due on that day every month.

  status           this month's rows: card, due, credited or not
  audit [--fix]    compare every row with its card; --fix creates and repairs
  sync [--since D] credit the block from Todoist's completion log (nightly
                   on Ix; idempotent, so it is also the backfill)

sync reads completions rather than hooking /did, so a card closed from dtd,
the phone or the Todoist app is credited the same way. A completion credits
the month of the occurrence's due date; a deferred copy credits the month in
its M.D stamp (tools/did/defer-fast.py stamps 5^2₦ copies).
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

MONO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MONO / "lib"))

from neon import monthly  # noqa: E402

LABEL = monthly.LABEL
DEFAULT_MINUTES = 20
# Domain label for a card this tool has to CREATE. Existing cards keep theirs.
DOMAINS = {
    "5^2 org": "f693", "5^2 s (上月的)": "g245", "5^2 g (5^3 checkin)": "g245",
    "date / fancy act": "xk88", "rent roll prelim check": "m5x2",
    "org validates business p&ls": "m5x2", "snail mail triage": "i447",
    "medium event w/lx (7+ ppl/3家)": "s897", "dealflow + 现金": "m5x2",
    "more hair": "hci", "xk87 habits + 学": "xk87", "toy / book 换": "xk87",
    "2 hcme upset review": "hcm", "5^0 g meta": "g245", "plan下月特日": "xk87",
    "special event (s)": "s897", "2 hci": "hci", "1 s meta": "g245",
    "streamline bags": "i447", "1 ₦ efficiency": "g245",
    "5^2 time audit + checksum for prev. month": "g245",
}
# Rows another skill runs; shown by status so the card says what to type.
SKILLS = {
    "5^2 time audit + checksum for prev. month": "/2n", "1 ₦ efficiency": "/2n1",
    "2 hci": "/2hci", "5^2 s (上月的)": "/2s",
}


def _norm(s) -> str:
    return monthly._norm(s)


# ── definitions ──────────────────────────────────────────────────────────────

def due_string(day) -> str:
    """'every 7th'; day 29+ is 'every last day' so February still has one."""
    d = int(day)
    if d >= 29:
        return "every last day"
    suf = "th" if 11 <= d % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
    return f"every {d}{suf}"


def occurrence(day, y: int, m: int) -> date:
    return date(y, m, min(31 if int(day) >= 29 else int(day), calendar.monthrange(y, m)[1]))


def first_due(day, today: date, card_due: date | None = None) -> date:
    """Where a (re)anchored card should next be due.
    - pushed into a later month: it was done for this one, so that month's day;
    - parked more than 45 days out: treated as having no date (2026-11-17, the
      org checkin sat at 2027-05 for five months);
    - still pending this month: left where it is, the recurrence snaps to the
      day from the next occurrence on;
    - overdue: this month's day, so it stays overdue."""
    if card_due and card_due > today + timedelta(days=45):
        card_due = None
    y, m = today.year, today.month
    if card_due and (card_due.year, card_due.month) > (y, m):
        return occurrence(day, card_due.year, card_due.month)
    d = occurrence(day, y, m)
    if d >= today or (card_due and card_due < today):
        return d
    if card_due:
        return card_due
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return occurrence(day, y, m)


def wanted_content(row: dict, existing: str | None = None) -> str:
    """'<col B> (C) [D]'. A blank C keeps the card's own (N)."""
    mins = row["minutes"]
    if mins is None and existing:
        m = re.search(r"\((\d+)\)", existing)
        mins = int(m.group(1)) if m else None
    if mins is None:
        mins = DEFAULT_MINUTES
    return f"{row['name']} ({int(mins)}) [{int(row['points'] or 0)}]"


def is_aliased(row: dict) -> bool:
    return _norm(row["name"]) in {_norm(monthly.MONTHLY_ALIASES[k]) for k in monthly.WIRED_CARD_NAMES}


# ── todoist ──────────────────────────────────────────────────────────────────

def _td():
    import todoist
    return todoist


def fetch_tasks() -> list[dict]:
    td, out, cursor = _td(), [], None
    while True:
        path = "/tasks?limit=200" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        page = td._request("GET", path)
        out += page.get("results", [])
        cursor = page.get("next_cursor")
        if not cursor:
            return out


def fetch_projects() -> dict[str, str]:
    td, out, cursor = _td(), {}, None
    while True:
        path = "/projects?limit=200" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        page = td._request("GET", path)
        for p in page.get("results", []):
            if not p.get("is_archived"):
                out.setdefault(p["name"], p["id"])
        cursor = page.get("next_cursor")
        if not cursor:
            return out


def fetch_completions(since: date) -> list[dict]:
    td, out, cursor = _td(), [], None
    while True:
        q = {"object_type": "item", "event_type": "completed", "limit": 100}
        if cursor:
            q["cursor"] = cursor
        page = td._request("GET", "/activities?" + urllib.parse.urlencode(q))
        evs = page.get("results", [])
        out += [e for e in evs if e.get("event_date", "")[:10] >= since.isoformat()]
        cursor = page.get("next_cursor")
        if not cursor or not evs or evs[-1].get("event_date", "")[:10] < since.isoformat():
            return out


def cards_by_row(rows: list[dict], tasks: list[dict]) -> dict[str, dict]:
    """{row name: {"parent": [recurring cards], "copies": [one-off cards]}}."""
    names = [r["name"] for r in rows]
    out = {n: {"parent": [], "copies": []} for n in names}
    for t in tasks:
        name, _ = monthly.row_name_for(t.get("content", ""), names)
        if name is None:
            continue
        recurring = bool((t.get("due") or {}).get("is_recurring"))
        out[name]["parent" if recurring else "copies"].append(t)
    return out


# ── audit ────────────────────────────────────────────────────────────────────

def audit(rows: list[dict], tasks: list[dict], today: date) -> list[dict]:
    """One finding per row: {"row", "name", "problems": [...], "fix": {...}|None}."""
    out = []
    cards = cards_by_row(rows, tasks)
    for r in rows:
        name, c = r["name"], cards[r["name"]]
        problems, fix = [], None
        want_due = due_string(r["day"]) if r["day"] else None
        if not r["day"] or r["points"] is None:
            problems.append("row has no day (col A) or no 分 (col D)")
        elif len(c["parent"]) > 1:
            problems.append("more than one recurring card: " + ", ".join(t["id"] for t in c["parent"]))
        elif not c["parent"]:
            problems.append("no recurring card")
            fix = {"op": "create", "content": wanted_content(r),
                   "labels": [LABEL, DOMAINS.get(_norm(name), "g245")],
                   "due_string": want_due, "due_date": first_due(r["day"], today).isoformat()}
        else:
            t = c["parent"][0]
            due = t.get("due") or {}
            body: dict = {}
            want = wanted_content(r, t["content"])
            if is_aliased(r):       # name is wired into code: only (N) [N] may change
                key = re.sub(r"\s*[\(\[\{].*$", "", t["content"]).strip()
                want = f"{key}{want[len(r['name']):]}"
            if t["content"] != want:
                problems.append(f"content '{t['content']}' ≠ '{want}'")
                body["content"] = want
            if LABEL not in (t.get("labels") or []):
                problems.append(f"missing label {LABEL}")
                body["labels"] = list(t.get("labels") or []) + [LABEL]
            if _norm(due.get("string", "")) != want_due:
                problems.append(f"due '{due.get('string', '')}' ≠ '{want_due}'")
                cur = date.fromisoformat(str(due["date"])[:10]) if due.get("date") else None
                body["due_string"] = want_due
                body["due_date"] = first_due(r["day"], today, cur).isoformat()
            if body:
                fix = {"op": "update", "id": t["id"], **body}
        out.append({"row": r["row"], "name": name, "problems": problems, "fix": fix,
                    "copies": len(c["copies"])})
    return out


def apply_fix(fix: dict, projects: dict[str, str]) -> str:
    td = _td()
    if fix["op"] == "create":
        domain = fix["labels"][-1]
        body = {"content": fix["content"], "labels": fix["labels"], "due_string": fix["due_string"]}
        if projects.get(domain):
            body["project_id"] = projects[domain]
        t = td._request("POST", "/tasks", body)
        td._request("POST", f"/tasks/{t['id']}", {"due_string": fix["due_string"], "due_date": fix["due_date"]})
        return t["id"]
    body = {k: v for k, v in fix.items() if k not in ("op", "id")}
    td._request("POST", f"/tasks/{fix['id']}", body)
    return fix["id"]


# ── sync ─────────────────────────────────────────────────────────────────────

def credits_from(events: list[dict], rows: list[dict], year: int) -> list[dict]:
    """Completions → [{"name", "month", "date", "content"}], one per row-month,
    for `year` only (the block's columns are one calendar year)."""
    names = [r["name"] for r in rows]
    seen, out = set(), []
    for e in sorted(events, key=lambda e: e.get("event_date", "")):
        x = e.get("extra_data") or {}
        content = x.get("content") or ""
        name, stamp = monthly.row_name_for(content, names)
        if name is None:
            continue
        done = date.fromisoformat(e["event_date"][:10])
        due_raw = x.get("completed_due_date_local") or x.get("completed_due_date") or ""
        due = date.fromisoformat(due_raw[:10]) if due_raw else done
        month, yr = due.month, due.year
        if stamp:                       # deferred copy: the occurrence it stood for
            month = stamp
            yr = done.year - 1 if stamp > done.month + 6 else done.year
        if yr != year or (name, month) in seen:
            continue
        seen.add((name, month))
        out.append({"name": name, "month": month, "date": done.isoformat(), "content": content})
    return out


def sync(rows: list[dict], events: list[dict], year: int, dry: bool = False,
         skip: tuple[str, ...] = ()) -> list[dict]:
    by_name = {r["name"]: r for r in rows}
    skipped = {_norm(n) for n in skip}
    out = []
    for c in credits_from(events, rows, year):
        if _norm(c["name"]) in skipped:
            continue
        r = by_name[c["name"]]
        if r["actuals"].get(c["month"]) not in (None, 0):
            continue
        if dry:
            out.append({**c, "value": r["points"], "dry": True})
            continue
        res = monthly.credit_monthly_cell(c["name"], c["month"], src=f"2neon sync: {c['content']} done {c['date']}")
        out.append({**c, "value": res.get("value"), "ok": res.get("ok"), "skipped": res.get("skipped"),
                    "cell": f"{res.get('col')}{res.get('row')}"})
    return out


# ── cli ──────────────────────────────────────────────────────────────────────

def cmd_status(today: date) -> int:
    rows = monthly.block_rows()
    cards = cards_by_row(rows, fetch_tasks())
    print(f"2₦ {today:%Y-%m}: day  分  done  card due    row")
    got = tot = 0
    for r in sorted(rows, key=lambda r: (r["day"] or 99, r["row"])):
        val = r["actuals"].get(today.month)
        tot += r["points"] or 0
        got += val or 0
        c = cards[r["name"]]
        due = ((c["parent"][0].get("due") or {}).get("date", "")[:10] if c["parent"] else "no card")
        extra = f"  +{len(c['copies'])} copy" if c["copies"] else ""
        skill = f"  {SKILLS[r['name']]}" if r["name"] in SKILLS else ""
        print(f"  {str(r['day'] or '?'):>3} {str(r['points']):>4}  {'✓' if val else '·'}    {due:<10}  {r['name']}{skill}{extra}")
    print(f"  credited {round(got)} of {round(tot)} 分")
    return 0


def cmd_audit(fix: bool, today: date) -> int:
    rows = monthly.block_rows()
    found = audit(rows, fetch_tasks(), today)
    bad = [f for f in found if f["problems"]]
    projects = fetch_projects() if fix and bad else {}
    for f in bad:
        print(f"row {f['row']} {f['name']}:")
        for p in f["problems"]:
            print(f"    {p}")
        if fix and f["fix"]:
            print(f"    fixed: {f['fix']['op']} {apply_fix(f['fix'], projects)}")
        elif f["fix"]:
            print("    would " + json.dumps(f["fix"], ensure_ascii=False))
    print(f"{len(found) - len(bad)} of {len(found)} rows correct" + ("" if fix or not bad else "; --fix to repair"))
    return 1 if bad and not fix else 0


def cmd_sync(since: date, dry: bool, today: date, skip: tuple[str, ...] = ()) -> int:
    rows = monthly.block_rows()
    res = sync(rows, fetch_completions(since), today.year, dry, skip)
    for c in res:
        state = "would credit" if c.get("dry") else ("credited" if c.get("ok") and not c.get("skipped") else f"NOT credited ({c.get('skipped') or 'write failed'})")
        print(f"{state}: {c['name']} month {c['month']} = {c['value']} (done {c['date']})")
    print(f"{len(res)} cell(s)")
    return 0 if all(c.get("dry") or c.get("ok") for c in res) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status")
    a = sub.add_parser("audit")
    a.add_argument("--fix", action="store_true")
    s = sub.add_parser("sync")
    s.add_argument("--since", type=date.fromisoformat)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--skip", action="append", default=[], metavar="ROW",
                   help="row name to leave alone (a backfill over months another column already credited)")
    args = ap.parse_args(argv)
    today = date.today()
    if args.cmd == "audit":
        return cmd_audit(args.fix, today)
    if args.cmd == "sync":
        return cmd_sync(args.since or today - timedelta(days=45), args.dry_run, today, tuple(args.skip))
    return cmd_status(today)


if __name__ == "__main__":
    sys.exit(main())
