"""2hci.py — monthly hci (image) review, the '2 hci' card.

Window: the 26th of the previous month through the 25th of the target month
(the review lands on the 26th, so the month it measures is offset like the
daily cards are). No arg = the most recent COMPLETED window: on/after the 26th
that ends on this month's 25th, before it the previous month's.

Reports:
  1st hci / 2nd hci — days the 0n cell earned its full row-2 weight. 0分's
      BF formula credits the weight for any NON-BLANK cell (=SUMPRODUCT(
      NOT(ISBLANK(D:M)), D$2:M$2)), so full = non-blank. An explicit 0 is
      reported separately as n/a (the vault convention), though Excel still
      credits it. Columns are found by row-1 header, never hardcoded.
  other hci — Todoist completions labelled hci in the window, minus the
      habit cards themselves (1st/2nd hci incl. dated copies, 2 hci) and
      defer records; points = each task's [N].

Usage:
  python3 2hci.py [month] [--no-mark] [--json]
  month: 10 | oct | 2026-10  (window ending on that month's 25th)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MONO = Path.home() / "i446-monorepo"
sys.path.insert(0, str(MONO / "lib"))

TZ = ZoneInfo("America/Los_Angeles")
IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.sh"
NEON_WB = "Neon分v12.2.xlsx"
HABITS = ["1st hci", "2nd hci"]
WINDOW_DAY = 26  # window starts on the 26th, ends on the 25th

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun",
          "jul", "aug", "sep", "oct", "nov", "dec"]

# Habit cards and bookkeeping that must not count as "other" image work.
HABIT_CARD_RX = re.compile(r"^\s*(1st hci|2nd hci|2 hci)\b", re.I)
POINTS_RX = re.compile(r"\[(\d+)\]")
# "[N]" with the @code bound to it (same grammar as lib/fen_split.PAIR_RE).
SPLIT_RX = re.compile(r"\[(\d+)\](?:[ \t]*@([\w\-一-鿿]+))?")


# ── window ───────────────────────────────────────────────────────────────────

def window_for(year: int, month: int) -> tuple[date, date]:
    """26th of the previous month → 25th of (year, month)."""
    end = date(year, month, WINDOW_DAY - 1)
    py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
    return date(py, pm, WINDOW_DAY), end


def default_window(today: date) -> tuple[date, date]:
    """Most recent completed window as of `today`."""
    if today.day >= WINDOW_DAY:
        return window_for(today.year, today.month)
    y, m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return window_for(y, m)


def parse_month(s: str, today: date) -> tuple[int, int]:
    s = s.strip().lower()
    m = re.match(r"^(\d{4})[-./](\d{1,2})$", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    if s.isdigit() and 1 <= int(s) <= 12:
        return today.year, int(s)
    for i, name in enumerate(MONTHS, 1):
        if s.startswith(name):
            return today.year, i
    sys.exit(f"ERROR: cannot parse month {s!r}")


# ── Neon 0n ──────────────────────────────────────────────────────────────────

def read_0n() -> tuple[dict, dict]:
    """One osascript on Ix: header-located 1st/2nd hci columns → their row-2
    weights and every data row's (M/D, value). Returns (weights, rows) with
    rows[habit] = {"M/D": "value"}."""
    finds = "\n".join(
        f'''        if h is "{h}" then set c{i} to c''' for i, h in enumerate(HABITS))
    script = f'''tell application "Microsoft Excel"
    set s to sheet "0n" of workbook "{NEON_WB}"
    set c0 to 0
    set c1 to 0
    repeat with c from 1 to 80
        set h to string value of (cell 1 of column c of s)
{finds}
    end repeat
    if c0 = 0 or c1 = 0 then return "ERROR: hci headers not found in 0n row 1"
    set out to (string value of (cell 2 of column c0 of s)) & "|" & (string value of (cell 2 of column c1 of s)) & linefeed
    repeat with r from 5 to 400
        set d to string value of (cell r of column 3 of s)
        if d is not "" then
            set out to out & d & "|" & (string value of (cell r of column c0 of s)) & "|" & (string value of (cell r of column c1 of s)) & linefeed
        end if
    end repeat
    return out
end tell'''
    p = subprocess.run([str(IX_OSA)], input=script, capture_output=True,
                       text=True, timeout=120)
    if p.returncode != 0 or p.stdout.startswith("ERROR"):
        sys.exit(f"ERROR: 0n read failed: {(p.stdout or p.stderr).strip()}")
    lines = p.stdout.replace("\r", "\n").strip().split("\n")
    w = lines[0].split("|")
    weights = {HABITS[i]: float(w[i] or 0) for i in range(2)}
    rows: dict[str, dict[str, str]] = {h: {} for h in HABITS}
    for ln in lines[1:]:
        parts = ln.split("|")
        if len(parts) < 3:
            continue
        for i, h in enumerate(HABITS):
            rows[h][parts[0].strip()] = parts[1 + i].strip()
    return weights, rows


def md(d: date) -> str:
    return f"{d.month}/{d.day}"


def score_habit(values: dict[str, str], start: date, end: date) -> dict:
    """Classify each day of the window: full (non-blank, non-zero), na
    (explicit 0), miss (blank). Dates absent from the sheet (prior year in a
    Dec→Jan window) are 'missing'."""
    out = {"full": 0, "na": 0, "miss": 0, "missing": 0, "days": 0, "minutes": 0}
    d = start
    while d <= end:
        out["days"] += 1
        key = md(d)
        if d.year != date.today().year or key not in values:
            out["missing"] += 1
        else:
            v = values[key]
            try:
                num = float(v)
            except ValueError:
                num = None
            if v == "":
                out["miss"] += 1
            elif num == 0:
                out["na"] += 1
            else:
                out["full"] += 1
                # A value > 1 is the session length in minutes; 1 = done, untimed.
                if num and num > 1:
                    out["minutes"] += int(num)
        d += timedelta(days=1)
    return out


# ── Todoist ──────────────────────────────────────────────────────────────────

def is_other_hci(item: dict) -> bool:
    content = item.get("content") or ""
    labels = item.get("labels") or []
    if "hci" not in labels:
        return False
    if content.lower().startswith("deferred:") or "posthoc" in labels:
        return False
    return not HABIT_CARD_RX.match(content)


def completed_hci(start: date, end: date) -> list[dict]:
    """hci-labelled completions in [start, end] PT. by_completion_date keeps
    labels (the plain /tasks/completed endpoint drops them)."""
    import todoist
    token = todoist._token()
    since = datetime(start.year, start.month, start.day, tzinfo=TZ)
    until = datetime(end.year, end.month, end.day, tzinfo=TZ) + timedelta(days=1)
    base = ("https://api.todoist.com/api/v1/tasks/completed/by_completion_date"
            f"?since={since.astimezone(ZoneInfo('UTC')):%Y-%m-%dT%H:%M:%SZ}"
            f"&until={until.astimezone(ZoneInfo('UTC')):%Y-%m-%dT%H:%M:%SZ}&limit=200")
    url, items, seen = base, [], set()
    while url:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        for it in data.get("items", []):
            key = (it.get("id"), it.get("completed_at"))
            if key in seen or not is_other_hci(it):
                continue
            seen.add(key)
            # hci's SHARE of the task: every [N] that is bare or bound to @hci
            # (a split task "[30] @hci [10] @i9", lib/fen_split.py, counts 30).
            pts = sum(int(n) for n, code in SPLIT_RX.findall(it.get("content") or "")
                      if code in ("", "hci"))
            done = datetime.fromisoformat(it["completed_at"].replace("Z", "+00:00")).astimezone(TZ)
            items.append({"content": it["content"], "points": pts,
                          "date": done.date().isoformat()})
        nxt = data.get("next_cursor")
        url = f"{base}&cursor={nxt}" if nxt else None
    items.sort(key=lambda x: x["date"])
    return items


# ── main ─────────────────────────────────────────────────────────────────────

MONTHLY_HEADER_ROW = 61   # 1n+: E61..P61 = 1..12 (month numbers)
MONTHLY_RANGE = "A61:P90"  # the 2₦ block: A = due day, B = habit, D = target, E:P = months


def credit_monthly_cell(month: int, points: int | None = None) -> dict:
    """Write the month's credit into the 2₦ block row whose B says '2 hci'
    (row 87 since 2026-11-09, when 2 hci left the weekly columns). Points
    default to the row's D target. Idempotent: a non-blank cell is left alone.
    Returns {"ok", "row", "col", "value", "skipped"?}."""
    sys.path.insert(0, str(MONO / "lib"))
    from neon import excel
    rng = excel.read_range("1n+", MONTHLY_RANGE)
    if not rng.get("ok"):
        return {"ok": False, "error": rng.get("error", "read_range failed")}
    first = rng.get("row", MONTHLY_HEADER_ROW)
    rows = rng["rows"]
    hdr = rows[MONTHLY_HEADER_ROW - first]
    col_idx = next((i for i, v in enumerate(hdr) if str(v).strip().rstrip(".0") == str(month)), None)
    row_idx = next((i for i, r in enumerate(rows) if len(r) > 1 and str(r[1]).strip().lower() == "2 hci"), None)
    if col_idx is None or row_idx is None:
        return {"ok": False, "error": f"2 hci monthly row/col not found (row={row_idx}, col={col_idx})"}
    row = first + row_idx
    col = chr(ord("A") + col_idx)
    target = rows[row_idx][3] if len(rows[row_idx]) > 3 else ""
    pts = points if points is not None else int(float(target or 0))
    current = rows[row_idx][col_idx] if len(rows[row_idx]) > col_idx else ""
    if str(current).strip() not in ("", "0", "0.0"):
        return {"ok": True, "row": row, "col": col, "value": current, "skipped": "already credited"}
    r = excel.batch_write("1n+", [{"col": col, "value": pts, "mode": "set"}], row=row,
                          src="2 hci monthly credit", fallback=False)
    return {"ok": bool(r.get("ok")), "row": row, "col": col, "value": pts, "raw": r}


def find_card_id() -> str | None:
    """The open '2 hci' monthly card (label 5^2₦ since 2026-11-09). did-fast
    only name-matches the 0neon/1neon/夜neon buckets, so the id is passed with
    --task-id and resolved directly."""
    try:
        sys.path.insert(0, str(MONO / "lib"))
        import todoist
        for t in todoist.find_tasks(labels=["5^2₦"], content_contains="2 hci", limit=10):
            if t.get("content", "").lower().startswith("2 hci"):
                return str(t["id"])
    except Exception:  # noqa: BLE001
        return None
    return None


def mark_done(month: int) -> None:
    """Self-clear the '2 hci' card through the normal /did path (Todoist close
    + 0分 points), then stamp the month's cell in the 1n+ 2₦ block."""
    tid = find_card_id()
    if tid:
        subprocess.run([sys.executable, str(MONO / "tools/did/did-fast.py"),
                        "--task-id", tid, "2 hci"], check=False)
    else:
        subprocess.run([sys.executable, str(MONO / "tools/did/run.py"), "2 hci"], check=False)
    try:
        r = credit_monthly_cell(month)
        tag = f"skipped ({r['skipped']})" if r.get("skipped") else ("ok" if r.get("ok") else f"FAILED {r.get('error')}")
        print(f"1n+ 2₦ {r.get('col')}{r.get('row')} = {r.get('value')}: {tag}")
    except Exception as e:  # noqa: BLE001 - the card is already closed; report, don't crash
        print(f"1n+ 2₦ monthly credit failed: {e}")
    subprocess.run([sys.executable, str(MONO / "tools/did/did-fast.py"), "--refresh-cache"],
                   check=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("month", nargs="?")
    ap.add_argument("--no-mark", action="store_true", help="report only; leave the card open")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    today = datetime.now(TZ).date()
    start, end = (window_for(*parse_month(a.month, today)) if a.month
                  else default_window(today))

    weights, rows = read_0n()
    habits = {h: score_habit(rows[h], start, end) for h in HABITS}
    other = completed_hci(start, end)
    other_pts = sum(t["points"] for t in other)

    if a.json:
        print(json.dumps({"start": start.isoformat(), "end": end.isoformat(),
                          "weights": weights, "habits": habits,
                          "other": other, "other_points": other_pts},
                         ensure_ascii=False, indent=2))
    else:
        print(f"2 hci  {start:%Y-%m-%d} → {end:%Y-%m-%d}  ({habits[HABITS[0]]['days']} days)")
        for h in HABITS:
            s = habits[h]
            extra = f", {s['na']} n/a" if s["na"] else ""
            extra += f", {s['missing']} not in sheet" if s["missing"] else ""
            pts = s["full"] * weights[h]
            print(f"  {h}: full {s['full']}/{s['days']} ({s['miss']} missed{extra})"
                  f"  = {pts:g}分 @ {weights[h]:g}  · {s['minutes']}m timed")
        print(f"  other hci: {other_pts}分 from {len(other)} task(s)")
        for t in other:
            print(f"    {t['date'][5:]}  [{t['points']}]  {t['content']}")
    if not a.no_mark:
        mark_done(end.month)


if __name__ == "__main__":
    main()
