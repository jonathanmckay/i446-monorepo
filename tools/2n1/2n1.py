"""2n1.py — monthly 1₦ efficiency review, the '5^2 1 n Efficiency Clearance' card.

Reports one month of the Neon 1n+ weekly habits as points earned / points
possible, and which habits cost the most. The rule is the one the sheet's
own `avg` column already applies on each month-anchor row (M.1):

    fixed column (row 5 is a number):  row-4 points for every week it was done
    rate column  (row 5 is text, 1/m): the cells themselves (minutes ≈ 分)
    efficiency = Σ earned / (weeks in month × row-4 Σ)

So this never writes `avg`; it recomputes it and flags a blank or a mismatch,
because /3s, /4gneon-s and /2026-4g-checkin read that cell.

Self-clearing: with no month argument, and the card due today or overdue, it
closes the card through did-fast (Todoist + 0分) and writes the row's target
into the 2₦ block row '1 ₦ efficiency' for the month the card was DUE in.
An explicit month is report-only: a second close of an already-advanced
monthly card would credit its points twice.

Publishing (2026-11-14): every run except --json writes the month's table
into the running note vault/g245/5e2/1n-efficiency.md (one `## YYYY-MM`
section per month, newest first; a rerun replaces that month's section) and
opens it in Obsidian unless --no-open.

Usage:
  python3 2n1.py [month] [--no-mark] [--no-open] [--json]
  month: 10 | oct | 2026-10   (default: the last completed month)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MONO = Path.home() / "i446-monorepo"
sys.path.insert(0, str(MONO / "lib"))
from neon import monthly as _monthly  # noqa: E402

TZ = ZoneInfo("America/Los_Angeles")
SHEET = "1n+"
SHEET_RANGE = "A1:AP60"
TARGET_ROW, RATE_ROW = 4, 5          # sheet rows: points per habit, fixed number or rate text
SUM_HEADER, AVG_HEADER = "∑", "avg"
CARD_NAME = "5^2 1 n Efficiency Clearance"
MONTHLY_HABIT = "1 ₦ efficiency"     # col B of the 2₦ block row this card credits
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
WEEK_RE = re.compile(r"^(\d{1,2})\.(\d)$")
MISMATCH = 0.0005
NOTE = Path.home() / "vault/g245/5e2/1n-efficiency.md"
NOTE_HEAD = """---
title: "1₦ Efficiency by Month"
date: {date}
type: report
tags: [g245, neon, 1n, efficiency]
source: 2n1.py
---
Monthly 1₦ efficiency from Neon `1n+`, written by `/2n1`: points earned over points possible, and the habits that cost the most. Newest month first.
"""


# ── month selection ──────────────────────────────────────────────────────────

def parse_month(s: str) -> int:
    s = s.strip().lower()
    m = re.match(r"^\d{4}[-./](\d{1,2})$", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(1))
    if s.isdigit() and 1 <= int(s) <= 12:
        return int(s)
    for i, name in enumerate(MONTHS, 1):
        if s.startswith(name):
            return i
    sys.exit(f"ERROR: cannot parse month {s!r}")


def default_month(today: date) -> int:
    """The last completed month."""
    return 12 if today.month == 1 else today.month - 1


# ── the efficiency rule ──────────────────────────────────────────────────────

def _num(v) -> float | None:
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def parse_sheet(rows: list[list[str]]) -> dict:
    """Locate everything by label: the habit columns sit between col B and the
    '∑' header; week rows are the ones whose col B reads M.W."""
    hdr = rows[0]
    sum_idx = next((i for i, v in enumerate(hdr) if str(v).strip() == SUM_HEADER), None)
    if sum_idx is None:
        raise ValueError(f"no '{SUM_HEADER}' header in row 1 of {SHEET}")
    avg_idx = next((i for i, v in enumerate(hdr) if str(v).strip().lower() == AVG_HEADER), None)
    cols = range(2, sum_idx)
    targets, rates = rows[TARGET_ROW - 1], rows[RATE_ROW - 1]
    total = _num(targets[sum_idx]) or sum(_num(targets[c]) or 0 for c in cols)
    weeks: dict[int, list[tuple[int, list[str]]]] = {}
    for r in rows:
        m = WEEK_RE.match(str(r[1]).strip()) if len(r) > 1 else None
        if m:
            weeks.setdefault(int(m.group(1)), []).append((int(m.group(2)), r))
    return {"headers": hdr, "cols": cols, "targets": targets, "rates": rates,
            "total": total, "weeks": weeks, "avg_idx": avg_idx}


def month_efficiency(sheet: dict, month: int) -> dict:
    wk = sorted(sheet["weeks"].get(month, []), key=lambda t: t[0])
    if not wk:
        raise ValueError(f"no {month}.N week rows in {SHEET}")
    n = len(wk)
    habits, earned_all = [], 0.0
    for c in sheet["cols"]:
        name = str(sheet["headers"][c]).strip()
        rate, target = str(sheet["rates"][c]), _num(sheet["targets"][c])
        cells = [str(r[c]) if len(r) > c else "" for _, r in wk]
        if _num(rate) is not None:                       # fixed: done or not
            done = sum(1 for v in cells if v.strip() != "" and _num(v) != 0)
            earned = done * (target or 0)
            missed = (n - done) * (target or 0)
            kind = "fixed"
        elif rate != "":                                 # rate: the cells are the points
            done = sum(1 for v in cells if (_num(v) or 0) != 0)
            earned = sum(_num(v) or 0 for v in cells)
            missed = max(0.0, n * target - earned) if target is not None else 0.0
            kind = "rate"
        else:
            continue
        earned_all += earned
        habits.append({"habit": name, "kind": kind, "done": done, "weeks": n,
                       "earned": earned, "missed": missed})
    possible = n * sheet["total"]
    anchor = next((r for w, r in wk if w == 1), None)
    ai = sheet["avg_idx"]
    sheet_avg = _num(anchor[ai]) if anchor is not None and ai is not None and len(anchor) > ai else None
    pct = earned_all / possible if possible else 0.0
    return {"month": month, "weeks": n, "earned": earned_all, "possible": possible,
            "pct": pct, "sheet_avg": sheet_avg,
            "avg_ok": sheet_avg is not None and abs(sheet_avg - pct) <= MISMATCH,
            "habits": sorted(habits, key=lambda h: -h["missed"])}


def render(e: dict, top: int | None = 10) -> str:
    out = [f"1₦ efficiency {MONTHS[e['month'] - 1]}: {e['pct']:.1%}  "
           f"({round(e['earned'])} of {round(e['possible'])} 分, {e['weeks']} weeks)"]
    if e["sheet_avg"] is None:
        out.append(f"WARNING: {SHEET} avg is blank on the {e['month']}.1 row; /3s will refuse until its formula is restored")
    elif not e["avg_ok"]:
        out.append(f"WARNING: {SHEET} avg on {e['month']}.1 reads {e['sheet_avg']:.1%}, not {e['pct']:.1%}; check its formula range")
    out += ["", "| Habit | Done | 分 missed |", "|---|---|---|"]
    for h in [h for h in e["habits"] if h["missed"] > 0][:top]:
        out.append(f"| {h['habit']} | {h['done']}/{h['weeks']} | {round(h['missed'])} |")
    return "\n".join(out)


# ── publishing ───────────────────────────────────────────────────────────────

def report_year(month: int, today: date) -> int:
    """The sheet holds one year of weeks; a month later than today's is last year's."""
    return today.year - 1 if month > today.month else today.year


def section(e: dict, year: int, today: date) -> str:
    lines = render(e, top=None).split("\n")
    return "\n".join([f"## {year}-{e['month']:02d}", "",
                      f"**{e['pct']:.1%}**: {round(e['earned'])} of {round(e['possible'])} 分 over "
                      f"{e['weeks']} weeks (run {today.isoformat()})"] + lines[1:]) + "\n"


def merge_note(existing: str, sec: str, today: date) -> str:
    """Put `sec` into the note: replace the section with the same `## YYYY-MM`
    heading, else insert it so sections stay newest first."""
    if not existing.strip():
        existing = NOTE_HEAD.format(date=today.isoformat())
    parts = re.split(r"(?m)^(?=## \d{4}-\d{2}\s*$)", existing)
    head, secs = parts[0].rstrip("\n") + "\n", {}
    for part in parts[1:]:
        secs[part.split("\n", 1)[0].strip()] = part.rstrip("\n") + "\n"
    secs[sec.split("\n", 1)[0].strip()] = sec.rstrip("\n") + "\n"
    return head + "\n" + "\n".join(secs[k] for k in sorted(secs, reverse=True))


def publish(e: dict, today: date, open_note: bool = True) -> Path:
    NOTE.parent.mkdir(parents=True, exist_ok=True)
    old = NOTE.read_text() if NOTE.exists() else ""
    NOTE.write_text(merge_note(old, section(e, report_year(e["month"], today), today), today))
    if open_note:
        rel = str(NOTE.relative_to(Path.home() / "vault")).replace("/", "%2F")
        subprocess.run(["open", f"obsidian://open?vault=vault&file={rel}"], check=False)
    return NOTE


# ── self-clearing ────────────────────────────────────────────────────────────

def _card_key(content: str) -> str:
    return " ".join(re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", content).split()).lower()


def find_card() -> dict | None:
    """The open card, with its due date. It carries the g245 label, not 5^2₦."""
    import todoist
    for t in todoist.find_tasks(labels=["g245"], limit=200):
        if _card_key(t.get("content", "")) == CARD_NAME.lower():
            return t
    return None


def mark_plan(card: dict | None, today: date) -> tuple[bool, int | None, str]:
    """(mark?, month to credit, reason). Mark only a card that is due: closing
    one that already advanced to next month would re-credit its points."""
    if card is None:
        return False, None, "card not open; nothing to close"
    due = str((card.get("due") or {}).get("date") or "")[:10]
    if not due:
        return False, None, "card has no due date; left open"
    d = date.fromisoformat(due)
    if d > today:
        return False, None, f"card is not due until {due} (already done this month); left alone"
    return True, d.month, f"due {due}"


def mark_done(card: dict, month: int) -> None:
    sys.stdout.flush()   # keep the report above did-fast's own lines
    subprocess.run([sys.executable, str(MONO / "tools/did/did-fast.py"),
                    "--task-id", str(card["id"]), CARD_NAME], check=False)
    try:
        r = _monthly.credit_monthly_cell(MONTHLY_HABIT, month, src="2n1 monthly credit")
        tag = (f"skipped ({r['skipped']})" if r.get("skipped")
               else "ok" if r.get("ok") else f"FAILED {r.get('error')}")
        print(f"{SHEET} 2₦ {r.get('col')}{r.get('row')} = {r.get('value')}: {tag}", flush=True)
    except Exception as e:  # noqa: BLE001 - the card is already closed; report, don't crash
        print(f"{SHEET} 2₦ monthly credit failed: {e}")
    subprocess.run([sys.executable, str(MONO / "tools/did/did-fast.py"), "--refresh-cache"],
                   check=False, stdout=subprocess.DEVNULL)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("month", nargs="?")
    ap.add_argument("--no-mark", action="store_true", help="report only; leave the card open")
    ap.add_argument("--no-open", action="store_true", help="publish the note without opening it")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    today = datetime.now(TZ).date()
    month = parse_month(a.month) if a.month else default_month(today)

    from neon import excel
    rng = excel.read_range(SHEET, SHEET_RANGE)
    if not rng.get("ok"):
        sys.exit(f"ERROR: cannot read {SHEET}: {rng.get('error', 'read_range failed')}")
    try:
        e = month_efficiency(parse_sheet(rng["rows"]), month)
    except ValueError as err:
        sys.exit(f"ERROR: {err}")
    print(json.dumps(e, ensure_ascii=False, indent=2) if a.json else render(e))

    if a.json:
        return
    try:
        print(f"\npublished: {publish(e, today, open_note=not a.no_open)}", flush=True)
    except OSError as err:
        print(f"\npublish failed: {err}", flush=True)
    if a.no_mark:
        return
    if a.month:
        print("\nreport only (explicit month); run bare /2n1 to close the card")
        return
    ok, credit_month, why = mark_plan(find_card(), today)
    if not ok:
        print(f"\n{why}")
        return
    print()
    mark_done(find_card(), credit_month)


if __name__ == "__main__":
    main()
