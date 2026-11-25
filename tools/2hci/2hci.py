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
from neon import monthly as _monthly  # noqa: E402 - bound before tests swap sys.modules["neon"]
from neon import excel as _excel  # noqa: E402 - excel-http daemon client (0n reads); bound here for the same reason

TZ = ZoneInfo("America/Los_Angeles")
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

# read_range returns cell VALUES as text: a real date cell in AppleScript's
# long form ("Wednesday, November 25, 2026 at 12:00:00\u202fAM"), a number as
# "26.0", where the old `string value` reads returned display text ("11/25",
# "26").
_LONG_DATE_RE = re.compile(r"^[A-Za-z]+, ([A-Za-z]+) (\d{1,2}), (\d{4}) at ")
_MONTH_NUM = {n: i for i, n in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}
_NUM_TEXT_RE = re.compile(r"^-?\d+\.\d+(?:[eE][-+]?\d+)?$")
HEADER_COLS = 80                 # row-1 headers are searched in columns 1..80
FIRST_DAY_ROW, LAST_DAY_ROW = 5, 400
DATE_COL = "C"


def _cell_date(text: str) -> date | None:
    """A real date cell as read_range returns it, else None."""
    m = _LONG_DATE_RE.match(text or "")
    if not m or m.group(1) not in _MONTH_NUM:
        return None
    try:
        return date(int(m.group(3)), _MONTH_NUM[m.group(1)], int(m.group(2)))
    except ValueError:
        return None


def _general(text: str) -> str:
    """A number value the way a General-format cell shows it ("15.0" → "15").
    Anything that is not a number value passes through untouched."""
    t = (text or "").strip()
    return "%.10g" % float(t) if _NUM_TEXT_RE.match(t) else (text or "")


def _letter(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _read(rng: str) -> list:
    r = _excel.read_range("0n", rng)
    rows = r.get("rows") if r.get("ok") else None
    if not isinstance(rows, list) or not rows:
        sys.exit(f"ERROR: 0n read failed: {r.get('error') or r}")
    return rows


def read_0n() -> tuple[dict, dict]:
    """Header-located 1st/2nd hci columns → their row-2 weights and every data
    row's (M/D, value), through the excel-http daemon (no raw AppleScript,
    2026-11-25). Returns (weights, rows) with rows[habit] = {"M/D": "value"}.
    A value is the cell's VALUE ("45", "" for blank), not its display text: a
    cell Excel shows as "##" (column too narrow) or in Arabic-Indic digits
    comes back as the plain number."""
    head = _read(f"A1:{_letter(HEADER_COLS)}2")
    if len(head) < 2:
        sys.exit("ERROR: 0n read failed: header rows missing")
    cols = [0, 0]
    for c, h in enumerate(head[0], start=1):
        for i, name in enumerate(HABITS):
            if str(h) == name:
                cols[i] = c                      # last match wins, as before
    if not all(cols):
        sys.exit("ERROR: 0n read failed: ERROR: hci headers not found in 0n row 1")
    weights = {HABITS[i]: float(str(head[1][cols[i] - 1]).strip() or 0) for i in range(2)}

    def column(letter: str) -> list:
        return [str(r[0]) if r else "" for r in
                _read(f"{letter}{FIRST_DAY_ROW}:{letter}{LAST_DAY_ROW}")]
    days = column(DATE_COL)
    vals = [column(_letter(c)) for c in cols]
    if not any(_cell_date(d) for d in days):
        # Dates that did not come back as dates would score the whole window
        # "not in sheet" without an error.
        sys.exit("ERROR: 0n read failed: no date cells in column C")
    rows: dict[str, dict[str, str]] = {h: {} for h in HABITS}
    for k, raw in enumerate(days):
        d = _cell_date(raw)
        key = f"{d.month}/{d.day}" if d else _general(raw).strip()
        if not key:
            continue
        for i, h in enumerate(HABITS):
            v = vals[i][k] if k < len(vals[i]) else ""
            rows[h][key] = _general(v).strip()
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

def credit_monthly_cell(month: int, points: int | None = None) -> dict:
    """Write the month's credit into the 2₦ block row whose B says '2 hci'
    (row 87 since 2026-11-09, when 2 hci left the weekly columns). Points
    default to the row's D target. Idempotent: a non-blank cell is left alone.
    Returns {"ok", "row", "col", "value", "skipped"?}. The writer itself is
    shared with /2n1 (lib/neon/monthly.py, 2026-11-14)."""
    return _monthly.credit_monthly_cell("2 hci", month, points, src="2 hci monthly credit")


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
