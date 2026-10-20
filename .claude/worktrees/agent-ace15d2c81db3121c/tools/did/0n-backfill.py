#!/usr/bin/env python3
"""0n-backfill.py — /0n: mark a 0₦ habit done on a PAST date, credit the points TODAY.

Usage:
    0n-backfill.py <habit> <date> [value] [--move-only] [--dry-run]

    <date>   09.18 | 9/18 | 9.18 | 2026-09-18 | yesterday   (must be a past date)
    [value]  cell value to write (default 1). Cumulative / variable habits
             (问学, xk20, 冥想, ...) are refused: their points are minute- or
             Toggl-derived and flow through 1n+, not through 0n row 2.
    --move-only  the 0n cell is ALREADY marked (by hand, or by an earlier run
             whose 0分 half failed): skip the 0n write and only relocate the
             row-2 weight from the past day to today.
    --dry-run    read everything, write nothing, print the plan.

Why a dedicated tool: Excel derives 0分 points from 0n by formula
(0n!BF = SUMPRODUCT(marks, row 2) → 0分!T 个, and similar chains for 媒/m5/i9/
xk/思). Marking a past row therefore credits the PAST day. The user's manual
fix was "mark the row, then subtract from that day and add to today". This
script does exactly that, atomically enough:

  1. ONE AppleScript (Excel serializes Apple Events per script, so nothing can
     interleave): read 0n row-2 weight + the target cell, read 0分 P..Z values
     on the past row (pre-image), set the 0n cell, `calculate`, re-read P..Z
     (post-image). Deltas = post - pre, per column. That captures every
     routing Excel actually applies (个 for most habits, 媒 for hcmc columns,
     m5 for m5x2 columns, and 0g's extra +9 on 0分!Q) without hard-coding
     the formulas.
  2. Sanity: Σdeltas must equal weight × value (0g may add exactly +9 on Q).
     Anything else → the 0n write is reverted in a second AppleScript and
     nothing moves. No half-states.
  3. Through the excel-http daemon (ledger `src: "0n backfill <habit> <date>"`):
     batch_append "-N" per changed column on the past date, "+N" on today.
     0分!D (Σ), E/F, G:O are never touched: D sums P:Y, today's live block
     formula absorbs the +N, and the past row's locked block cells are
     unaffected because the past row nets to zero.
  4. Save workbook, refresh the dashboard points cache (same refresher /0t
     uses), print JSON.

Never stamps the 0l/0t "N color" time (0n!AF) — that is a same-day ritual
stamp and would drag the 111-point all-colors bonus with it.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# --- project imports (by path, same pattern as 0t-fast.py) ------------------
_IX_PATH = Path.home() / ".claude/skills/_lib/ix-osa.py"
_IX_SPEC = importlib.util.spec_from_file_location("ix_osa", _IX_PATH)
_ix_mod = importlib.util.module_from_spec(_IX_SPEC)
sys.modules["ix_osa"] = _ix_mod
_IX_SPEC.loader.exec_module(_ix_mod)
ix_run = _ix_mod.run

sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
from neon import excel as neon_excel  # noqa: E402
import daytime  # noqa: E402

_DID_PATH = Path(__file__).resolve().parent / "did-fast.py"

WORKBOOK = "Neon分v12.2.xlsx"
FEN_COLS = list(range(16, 27))  # 0分 P..Z
FEN_NAMES = {16: "-1₦", 17: "0g", 18: "i9", 19: "m5", 20: "个", 21: "媒",
             22: "思", 23: "hcb", 24: "xk", 25: "社", 26: "n156"}
# 0n column range → 0分 column that its row-2 weight flows to (used only for
# --move-only, where nothing is written to 0n and so no delta can be measured).
RANGE_TO_FEN = [((4, 13), 20), ((17, 20), 20),   # D:M, Q:T → 个
                ((14, 16), 21),                   # N:P   → 媒
                ((27, 29), 19),                   # AA:AC → m5
                ((21, 26), 18),                   # U:Z   → i9
                ((36, 41), 24),                   # AJ:AO → xk
                ((42, 45), 22)]                   # AP:AS → 思


def _did():
    spec = importlib.util.spec_from_file_location("did_fast_for_0n", _DID_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_for_0n"] = mod
    spec.loader.exec_module(mod)
    return mod


# --- pure helpers (unit-tested) ---------------------------------------------
def col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def parse_target_date(raw: str, today: date) -> date:
    """'09.18' | '9/18' | '9.18' | '2026-09-18' | 'yesterday' → date.
    M/D with no year picks last year when that M/D is after today."""
    raw = raw.strip().lower()
    if raw == "yesterday":
        return today - timedelta(days=1)
    if len(raw) == 10 and raw[4] == "-":
        return date.fromisoformat(raw)
    sep = "." if "." in raw else "/"
    m, d = (int(p) for p in raw.split(sep))
    y = today.year
    if (m, d) > (today.month, today.day):
        y -= 1
    return date(y, m, d)


def fmt_num(x: float) -> str:
    """26.0 → '26', 7.5 → '7.5' (formula-append hygiene: no '26.0', no '--')."""
    return str(int(round(x))) if abs(x - round(x)) < 1e-9 else f"{x:g}"


def signed(x: float) -> str:
    return ("+" if x >= 0 else "-") + fmt_num(abs(x))


def parse_num(s: str) -> float:
    s = (s or "").strip()
    if s in ("", "missing value"):
        return 0.0
    try:
        return float(s)
    except ValueError as e:  # '#REF!', '#VALUE!', text
        raise ValueError(f"non-numeric cell value {s!r}") from e


def compute_deltas(pre: dict[int, float], post: dict[int, float]) -> dict[int, float]:
    out = {}
    for c in FEN_COLS:
        d = round(post.get(c, 0.0) - pre.get(c, 0.0), 2)
        if abs(d) >= 0.005:
            out[c] = d
    return out


def deltas_match_expected(deltas: dict[int, float], expected: float,
                          habit_col: int) -> bool:
    """Σdeltas == weight × value, except the 0g habit (0n!T = col 20) which also
    adds exactly +9 on 0分!Q via IF('0n'!T<>0,9,0)."""
    total = round(sum(deltas.values()), 2)
    if abs(total - expected) < 0.005:
        return True
    if habit_col == 20 and deltas.get(17) == 9 and abs(total - expected - 9) < 0.005:
        return True
    return False


def fen_col_for_habit(habit_col: int) -> int:
    for (lo, hi), fen in RANGE_TO_FEN:
        if lo <= habit_col <= hi:
            return fen
    return 20


def week_start(d: date) -> date:
    """Sunday-anchored week (matches did-fast calc_week_mw)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


# --- AppleScript -------------------------------------------------------------
def _row_lookup(var: str, sheet_var: str, date_col: int, month: int, day: int) -> str:
    return f'''    set {var} to 0
    repeat with r from 3 to 500
        set cd to value of cell {date_col} of row r of {sheet_var}
        if cd is not missing value then
            try
                if ((month of (cd as date)) as integer) = {month} and (day of (cd as date)) = {day} then
                    set {var} to r
                    exit repeat
                end if
            end try
        end if
    end repeat'''


def _fen_read_block(tag: str) -> str:
    lines = []
    for c in FEN_COLS:
        lines.append(f'''    set fv to value of cell {c} of row fRow of wf
    if fv is missing value then set fv to ""
    set out to out & "{tag}" & (character id 9) & "{c}" & (character id 9) & (fv as text) & linefeed''')
    return "\n".join(lines)


def build_script(habit_col: int, month: int, day: int, value, do_write: bool) -> str:
    """One script: weight, pre cell, 0分 pre-image, [write + calculate], post-image."""
    write_block = ""
    if do_write:
        # Guard inside the script: a non-empty cell is never overwritten, so
        # "already marked" needs no revert round-trip.
        write_block = f'''    if (pv as text) = "" then
        set value of cell {habit_col} of row nRow of ws to {value}
        calculate
        set pv2 to value of cell {habit_col} of row nRow of ws
        if pv2 is missing value then set pv2 to ""
        set out to out & "POST0N" & (character id 9) & (pv2 as text) & linefeed
{_fen_read_block("POST").replace(chr(10) + "    ", chr(10) + "        ")}
    else
        set out to out & "SKIPPED" & linefeed
    end if'''
    return f'''tell application "Microsoft Excel"
    set wb to workbook "{WORKBOOK}"
    set ws to sheet "0n" of wb
    set wf to sheet "0分" of wb
    set out to ""
{_row_lookup("nRow", "ws", 3, month, day)}
{_row_lookup("fRow", "wf", 2, month, day)}
    if nRow = 0 then return "ERROR: date {month}/{day} not found in 0n"
    if fRow = 0 then return "ERROR: date {month}/{day} not found in 0分"
    set out to out & "ROWS" & (character id 9) & nRow & (character id 9) & fRow & linefeed
    set wv to value of cell {habit_col} of row 2 of ws
    if wv is missing value then set wv to ""
    set out to out & "WEIGHT" & (character id 9) & (wv as text) & linefeed
    set pv to value of cell {habit_col} of row nRow of ws
    if pv is missing value then set pv to ""
    set out to out & "PRE0N" & (character id 9) & (pv as text) & linefeed
{_fen_read_block("PRE")}
{write_block}
    return out
end tell'''


def build_revert_script(habit_col: int, n_row: int, pre_raw: str) -> str:
    if pre_raw == "":
        restore = f"clear contents cell {habit_col} of row {n_row} of ws"
    else:
        restore = f"set value of cell {habit_col} of row {n_row} of ws to {pre_raw}"
    return f'''tell application "Microsoft Excel"
    set ws to sheet "0n" of workbook "{WORKBOOK}"
    {restore}
    calculate
    return "reverted"
end tell'''


def parse_script_output(stdout: str) -> dict:
    d: dict = {"PRE": {}, "POST": {}}
    for line in stdout.splitlines():
        parts = line.rstrip("\n").split("\t")
        tag = parts[0]
        if tag == "ROWS":
            d["n_row"], d["f_row"] = int(parts[1]), int(parts[2])
        elif tag in ("WEIGHT", "PRE0N", "POST0N"):
            d[tag] = parts[1] if len(parts) > 1 else ""
        elif tag in ("PRE", "POST"):
            d[tag][int(parts[1])] = parse_num(parts[2] if len(parts) > 2 else "")
    return d


# --- side effects --------------------------------------------------------------
def save_and_refresh() -> str:
    ix_run(f'tell application "Microsoft Excel" to save workbook "{WORKBOOK}"', timeout=15.0)
    script = str(Path.home() / "i446-monorepo/tools/personal-dashboard/refresh-points-cache.sh")
    host_file = Path.home() / ".claude" / ".host-name"
    on_ix = host_file.exists() and host_file.read_text().strip() == "ix"
    cmd = ["bash", script] if on_ix else \
        ["ssh", "-o", "ConnectTimeout=20", "-o", "BatchMode=yes", "ix", f"bash {script}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return "ERROR: " + ((r.stderr or r.stdout).strip() or f"exit {r.returncode}")
    return r.stdout.strip()


def resolve_habit(df, habit: str) -> tuple[str, int]:
    key = df.header_normalize(habit)
    headers = df.load_headers()["0n"]
    if key not in headers:
        headers = df.refresh_headers()["0n"]
    if key not in headers:
        close = [h for h in headers if key in h or h in key]
        raise SystemExit(f"ERROR: habit {habit!r} not a 0n header"
                         + (f"; close: {close}" if close else ""))
    return key, headers[key]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("habit")
    ap.add_argument("date")
    ap.add_argument("value", nargs="?", default="1")
    ap.add_argument("--move-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    today = daytime.today()
    target = parse_target_date(a.date, today)
    if target >= today:
        raise SystemExit(f"ERROR: {target} is not a past date; use /did for today")

    df = _did()
    key, col = resolve_habit(df, a.habit)
    if key in df.CUMULATIVE_0N or key in df.VARIABLE_0N:
        raise SystemExit(f"ERROR: {key!r} is a cumulative/variable habit (minute- or "
                         "Toggl-valued, flows via 1n+); /0n only handles fixed row-2 habits")
    try:
        value = int(a.value) if float(a.value).is_integer() else float(a.value)
    except ValueError:
        raise SystemExit(f"ERROR: value {a.value!r} is not numeric")

    src = f"0n backfill {key} {target.isoformat()}"
    past_md = f"{target.month}/{target.day}"
    today_md = f"{today.month}/{today.day}"
    out: dict = {"habit": key, "0n_col": col_letter(col), "date": target.isoformat(),
                 "today": today.isoformat(), "value": value, "src": src}
    if week_start(target) != week_start(today):
        out["note"] = (f"cross-week move: {past_md} is in the week of "
                       f"{week_start(target).isoformat()}; that week's 1s totals change")

    # 1. read (+ write) in one AppleScript
    do_write = not (a.move_only or a.dry_run)
    res = ix_run(build_script(col, target.month, target.day, value, do_write), timeout=60.0)
    if res.returncode != 0 or res.stdout.startswith("ERROR"):
        raise SystemExit(f"ERROR: excel read failed: {res.stderr or res.stdout}")
    r = parse_script_output(res.stdout)
    weight = parse_num(r.get("WEIGHT", ""))
    pre0n = r.get("PRE0N", "")
    out.update({"weight": weight, "pre_0n": pre0n, "rows": [r["n_row"], r["f_row"]]})

    if weight <= 0:
        if do_write and pre0n == "":  # already written: revert, nothing to move anyway
            ix_run(build_revert_script(col, r["n_row"], pre0n), timeout=30.0)
        raise SystemExit(f"ERROR: {key!r} has no row-2 weight ({weight}); nothing to move")

    # 2. plan the move
    if a.move_only:
        if pre0n == "":
            raise SystemExit(f"ERROR: --move-only but 0n!{col_letter(col)} on {past_md} is empty; "
                             "run without --move-only")
        deltas = {fen_col_for_habit(col): weight * value}
        out["basis"] = "row-2 weight (move-only, no delta measured)"
    elif a.dry_run:
        if pre0n != "":
            out["warning"] = f"0n cell already = {pre0n}; a real run will refuse (use --move-only)"
        deltas = {fen_col_for_habit(col): weight * value}
        out["basis"] = "row-2 weight (dry-run estimate)"
    else:
        if pre0n != "":
            # the script skipped the write (guarded in AppleScript)
            raise SystemExit(f"ERROR: 0n!{col_letter(col)} on {past_md} already = {pre0n}. "
                             f"If its points still sit on {past_md}, rerun with --move-only.")
        deltas = compute_deltas(r["PRE"], r["POST"])
        expected = weight * value
        if not deltas_match_expected(deltas, expected, col):
            ix_run(build_revert_script(col, r["n_row"], pre0n), timeout=30.0)
            raise SystemExit("ERROR: measured 0分 change "
                             f"{ {FEN_NAMES[c]: v for c, v in deltas.items()} } != expected "
                             f"{expected} for {key}; 0n write reverted, nothing moved")
        out["basis"] = "measured 0分 delta"
        out["post_0n"] = r.get("POST0N", "")

    moves = [{"col": col_letter(c), "name": FEN_NAMES[c], "pts": v} for c, v in deltas.items()]
    out["moves"] = moves
    if a.dry_run:
        out["dry_run"] = True
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    # 3. relocate through the daemon (ledger-journaled)
    back = neon_excel.batch_append("0分", [(col_letter(c), signed(-v)) for c, v in deltas.items()],
                                   date=past_md, src=src)
    out["back_out"] = {"date": past_md, "ok": bool(back.get("ok")), "row": back.get("row"),
                       "error": back.get("error")}
    if not back.get("ok"):
        out["recovery"] = (f"0n is marked but points not moved. Rerun: 0n-backfill.py "
                           f"{key!r} {target.isoformat()} {value} --move-only")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 2
    fwd = neon_excel.batch_append("0分", [(col_letter(c), signed(v)) for c, v in deltas.items()],
                                  date=today_md, src=src)
    out["credit"] = {"date": today_md, "ok": bool(fwd.get("ok")), "row": fwd.get("row"),
                     "error": fwd.get("error")}
    if not fwd.get("ok"):
        out["recovery"] = (f"{past_md} backed out but today's credit failed. Append "
                           f"{[(m['col'], signed(m['pts'])) for m in moves]} to 0分 {today_md} "
                           f"(src {src!r}) via neon.excel.batch_append")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 2

    # 4. save + dashboard
    try:
        out["dashboard"] = save_and_refresh()
    except Exception as e:  # noqa: BLE001
        out["dashboard"] = f"ERROR: {e}"
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
