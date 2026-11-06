#!/usr/bin/env python3
"""0n-backfill.py — /0n: mark a 0₦ habit done on its own row (past, today, or
tomorrow during 戌/亥), credit the points TODAY.

Usage:
    0n-backfill.py <habit> <date> [value] [--move-only] [--dry-run]

    <date>   09.18 | 9/18 | 9.18 | 2026-09-18 | yesterday | tomorrow
             Usually a past date. TODAY is also fine: the mark lands on
             today's row and Excel credits today directly, so nothing moves.
             TOMORROW is allowed only during 戌/亥 (18:00-22:00 local), so the
             next day's row can be pre-marked at wind-down with the points
             still landing today (2026-10-17, per JM). Points ALWAYS land on
             today, whichever day the mark goes to.
    [value]  cell value to write (default 1). Variable / cumulative habits
             (hiit, 冥想, 问学, xk20, ...) take minutes: '+N' adds to the cell,
             '=N' sets it, bare N adds for CUMULATIVE_0N and sets otherwise
             (same as /did). Whatever 0分 change Excel makes on that row is
             measured and moved; none (points only via weekly 1n+) = nothing
             moves. Habit and date may come in either order.
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
import os
import re
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
import blocks  # noqa: E402
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
    """'09.18' | '9/18' | '9.18' | '2026-09-18' | 'today' | 'yesterday' | 'tomorrow' → date.
    M/D with no year: tomorrow's own M/D means tomorrow (advance marking,
    gated to 戌/亥 in main); any other M/D after today picks last year.
    Last year's tomorrow-date needs the ISO form."""
    raw = raw.strip().lower()
    if raw == "today":
        return today
    if raw == "yesterday":
        return today - timedelta(days=1)
    if raw == "tomorrow":
        return today + timedelta(days=1)
    if len(raw) == 10 and raw[4] == "-":
        return date.fromisoformat(raw)
    sep = "." if "." in raw else "/"
    m, d = (int(p) for p in raw.split(sep))
    tomorrow = today + timedelta(days=1)
    if (m, d) == (tomorrow.month, tomorrow.day):
        return tomorrow
    y = today.year
    if (m, d) > (today.month, today.day):
        y -= 1
    return date(y, m, d)


def looks_like_date(raw: str) -> bool:
    try:
        parse_target_date(raw, date(2026, 6, 15))
        return True
    except (ValueError, TypeError):
        return False


def order_args(a: str, b: str) -> tuple[str, str]:
    """(habit, date) in either order: '/0n 11.02 hiit +15' works like
    '/0n hiit 11.02 +15' (2026-11-03). Habit headers never parse as dates."""
    if looks_like_date(a) and not looks_like_date(b):
        return b, a
    return a, b


def split_args(a: str, b: str, value: str | None) -> tuple[str, str, str]:
    """(habit, date, value). '/0n nvc 15' has no date: a bare whole number can't
    be one (dates carry . / or -), so it is the value, for today (2026-11-06:
    it landed in the date slot and crashed, so nothing was written)."""
    if value is None:
        for habit, raw in ((a, b), (b, a)):
            if re.fullmatch(r"[+=]?\d+", raw):
                return habit, "today", raw
    habit, day = order_args(a, b)
    return habit, day, "1" if value is None else value


def parse_value(raw: str, cumulative: bool) -> tuple[str, float]:
    """'+N' → add, '=N' → set, bare N → add for CUMULATIVE_0N habits (same
    as /did, which adds for them) and set for everything else."""
    s = raw.strip()
    if s.startswith("+"):
        mode, s = "add", s[1:]
    elif s.startswith("="):
        mode, s = "set", s[1:]
    else:
        mode = "add" if cumulative else "set"
    try:
        n = float(s)
    except ValueError:
        raise ValueError(f"value {raw!r} is not numeric") from None
    return mode, (int(n) if n.is_integer() else n)


def deltas_follow_change(deltas: dict[int, float], change: float) -> bool:
    """Variable habits have no row-2 model to check against, so the measured
    0分 change is moved as-is. Guard: every column moved the same way the cell
    did (a stray recalc of an unrelated column shows up as a wrong sign)."""
    if change == 0:
        return not deltas
    return all((v > 0) == (change > 0) for v in deltas.values())


def advance_window_open(now: datetime) -> bool:
    """Tomorrow may be marked early only during 戌/亥 — the two evening
    wind-down blocks (18:00-22:00 local on the 04:00-wake schedule)."""
    return blocks.BLOCK_START["戌"] <= now.hour < blocks.BLOCK_START["亥"] + 2


def fmt_num(x: float) -> str:
    """26.0 → '26', 7.5 → '7.5' (formula-append hygiene: no '26.0', no '--')."""
    return str(int(round(x))) if abs(x - round(x)) < 1e-9 else f"{x:g}"


def signed(x: float) -> str:
    return ("+" if x >= 0 else "-") + fmt_num(abs(x))


def already_credited(pre0n: str) -> bool:
    """True when the 0n cell already holds a mark: any value other than blank
    or 0 (2026-09-28, per JM: /0n on an already-marked day just reports that
    credit was taken -- no need to check Neon first)."""
    txt = (pre0n or "").strip()
    if txt == "":
        return False
    try:
        return float(txt) != 0
    except ValueError:
        return True  # non-numeric text in the cell still counts as marked


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


def build_script(habit_col: int, month: int, day: int, value, do_write: bool,
                 variable_mode: str | None = None) -> str:
    """One script: weight, pre cell, 0分 pre-image, [write + calculate], post-image.
    variable_mode ('add'/'set') = variable/cumulative habit: no blank-only
    guard, and `calculate full` because its points route through another
    sheet (hiit: 0n!AE → hcbi!Y → 0分!W)."""
    write_block = ""
    if do_write and variable_mode:
        if variable_mode == "add":
            assign = f'''    if (pv as text) = "" or (pv as text) = "0" or (pv as text) = "0.0" then
        set nv to {value}
    else
        set nv to (pv as number) + {value}
    end if'''
        else:
            assign = f"    set nv to {value}"
        write_block = f'''{assign}
    set value of cell {habit_col} of row nRow of ws to nv
    calculate full
    set pv2 to value of cell {habit_col} of row nRow of ws
    if pv2 is missing value then set pv2 to ""
    set out to out & "POST0N" & (character id 9) & (pv2 as text) & linefeed
{_fen_read_block("POST")}'''
    elif do_write:
        # Guard inside the script: a non-empty cell is never overwritten, so
        # "already marked" needs no revert round-trip.
        write_block = f'''    if (pv as text) = "" or (pv as text) = "0" or (pv as text) = "0.0" then
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


def build_remeasure_script(month: int, day: int) -> str:
    """`calculate full`, then the 0分 post-image again (2026-10-22: with Excel's
    dependency recalc stalled, a plain `calculate` left 0分 unchanged, so 45
    marks measured {} and were 'reverted'; only `calculate full` recalculates
    through a stall)."""
    return f'''tell application "Microsoft Excel"
    set wb to workbook "{WORKBOOK}"
    set wf to sheet "0分" of wb
    set out to ""
{_row_lookup("fRow", "wf", 2, month, day)}
    if fRow = 0 then return "ERROR: date {month}/{day} not found in 0分"
    calculate full
{_fen_read_block("POST")}
    return out
end tell'''


def build_revert_script(habit_col: int, n_row: int, pre_raw: str) -> str:
    """Restore the 0n cell and REPORT what it holds afterwards, so a revert
    that did not land is caught instead of claimed (2026-10-22: the message
    said 'reverted' while the cell stayed 1)."""
    if pre_raw == "":
        restore = f"clear contents cell {habit_col} of row {n_row} of ws"
    else:
        restore = f"set value of cell {habit_col} of row {n_row} of ws to {pre_raw}"
    return f'''tell application "Microsoft Excel"
    set ws to sheet "0n" of workbook "{WORKBOOK}"
    {restore}
    calculate
    set rv to value of cell {habit_col} of row {n_row} of ws
    if rv is missing value then set rv to ""
    return "REVERTED" & (character id 9) & (rv as text)
end tell'''


def revert_landed(stdout: str, pre_raw: str) -> bool:
    """True when the revert script reports the cell back at its pre-value."""
    for line in stdout.splitlines():
        parts = line.split("\t")
        if parts[0] == "REVERTED":
            now = parts[1].strip() if len(parts) > 1 else ""
            if pre_raw in ("", "0", "0.0"):
                return now in ("", "0", "0.0")
            return parse_num(now) == parse_num(pre_raw)
    return False


def revert_or_die(habit_col: int, n_row: int, pre_raw: str, why: str, where: str,
                  src: str = "0n backfill revert") -> None:
    ok, now, err = daemon_set(habit_col, where, pre_raw, f"{src} (revert)")
    landed = ok and (parse_num(now) == parse_num(pre_raw) if pre_raw not in ("", "0", "0.0")
                     else now in ("", "0", "0.0"))
    if landed:
        raise SystemExit(f"ERROR: {why}; 0n write reverted, nothing moved")
    raise SystemExit(f"ERROR: {why}; REVERT DID NOT LAND ({(err or now)[:160]}): "
                     f"0n!{col_letter(habit_col)} on {where} may still hold the mark and its points "
                     "sit on that day. Check the cell; if it is marked, rerun with --move-only.")


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
def planned_value(pre_raw: str, value: float, vmode: str | None) -> float | None:
    """The 0n cell's new value, or None when a plain mark is already there
    (same guard the AppleScript used to apply: a set mark is never
    overwritten). vmode 'add' = cumulative minutes, 'set' = replace."""
    if vmode is None:
        return None if already_credited(pre_raw) else value
    if vmode == "add":
        return parse_num(pre_raw) + value
    return value


def daemon_set(habit_col: int, md: str, value, src: str) -> tuple[bool, str, str]:
    """Set the 0n cell through the excel-http daemon (journaled, chain intact;
    2026-11-04: the AppleScript write was invisible to the ledger, so the
    strict check read every /0n mark as typed into 0n). '' clears it.
    Returns (ok, value now in the cell, error)."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    try:
        r = neon_excel.batch_write("0n", [{"col": col_letter(habit_col), "value": value, "mode": "set"}],
                                   date=md, src=src)
    except Exception as e:  # noqa: BLE001
        return False, "", str(e)
    res = (r.get("results") or [{}])[0]
    return bool(r.get("ok")), str(res.get("value", "")), str(r.get("error") or "")


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
    # Never read stdin (2026-10-22: run inside `while read d; do ... done`, a
    # child here ate the loop's input and dates were silently skipped).
    try:
        os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    except OSError:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("habit")
    ap.add_argument("date")
    ap.add_argument("value", nargs="?")
    ap.add_argument("--move-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    a.habit, a.date, a.value = split_args(a.habit, a.date, a.value)
    today = daytime.today()
    try:
        target = parse_target_date(a.date, today)
    except ValueError:
        raise SystemExit(f"ERROR: {a.date!r} isn't a date; use 9/18, 2026-09-18, today or yesterday")
    tomorrow = today + timedelta(days=1)
    if target > tomorrow:
        raise SystemExit(f"ERROR: {target} is more than one day ahead; "
                         "/0n reaches at most tomorrow (during 戌/亥)")
    if target == tomorrow and not advance_window_open(daytime.local_now()):
        raise SystemExit(f"ERROR: {target} is tomorrow; marking tomorrow is only "
                         "allowed during 戌/亥 (18:00-22:00 local)")
    in_place = target == today  # mark today's row: Excel credits today, no move
    if in_place and a.move_only:
        raise SystemExit("ERROR: --move-only with today's date is meaningless; "
                         "the points already sit on today")

    df = _did()
    key, col = resolve_habit(df, a.habit)
    # Variable/cumulative habits (hiit, 冥想, xk20, ...): minutes, not a
    # row-2 mark. Their points reach the day's 0分 through formulas of their
    # own (hiit: 0n!AE*2 → hcbi!Y → 0分!W), so the measured delta is moved
    # as-is (2026-11-03: '/0n 11.02 hiit +15' used to be refused).
    variable = key in df.CUMULATIVE_0N or key in df.VARIABLE_0N
    try:
        if variable:
            vmode, value = parse_value(a.value, key in df.CUMULATIVE_0N)
        else:
            vmode, value = None, parse_value(a.value, False)[1]
    except ValueError as e:
        raise SystemExit(f"ERROR: {e}")
    if variable and a.move_only:
        raise SystemExit(f"ERROR: --move-only can't size a variable habit's points ({key!r} has "
                         "no row-2 weight model); move them with neon.excel.batch_append")

    src = f"0n backfill {key} {target.isoformat()}"
    past_md = f"{target.month}/{target.day}"
    today_md = f"{today.month}/{today.day}"
    out: dict = {"habit": key, "0n_col": col_letter(col), "date": target.isoformat(),
                 "today": today.isoformat(), "value": value, "src": src}
    if variable:
        out["mode"] = vmode
    if week_start(target) != week_start(today):
        out["note"] = (f"cross-week move: {past_md} is in the week of "
                       f"{week_start(target).isoformat()}; that week's 1s totals change")

    # 1. read the weight, the cell and the 0分 pre-image (read-only script)
    do_write = not (a.move_only or a.dry_run)
    res = ix_run(build_script(col, target.month, target.day, value, False), timeout=60.0)
    if res.returncode != 0 or res.stdout.startswith("ERROR"):
        raise SystemExit(f"ERROR: excel read failed: {res.stderr or res.stdout}")
    r = parse_script_output(res.stdout)
    weight = parse_num(r.get("WEIGHT", ""))
    pre0n = r.get("PRE0N", "")
    out.update({"weight": weight, "pre_0n": pre0n, "rows": [r["n_row"], r["f_row"]]})

    if weight <= 0 and not variable:
        raise SystemExit(f"ERROR: {key!r} has no row-2 weight ({weight}); nothing to move")

    # 1b. write the mark through the daemon, then measure Excel's 0分 change
    if do_write:
        nv = planned_value(pre0n, value, vmode)
        if nv is not None:
            ok, now, err = daemon_set(col, past_md, nv, src)
            if not ok:
                raise SystemExit(f"ERROR: 0n write failed ({err or now}); nothing written, nothing moved")
            r["POST0N"] = now
            rm = ix_run(build_remeasure_script(target.month, target.day), timeout=90.0)
            if rm.returncode != 0 or rm.stdout.startswith("ERROR"):
                revert_or_die(col, r["n_row"], pre0n, f"0分 re-read failed: {rm.stderr or rm.stdout}",
                              past_md, src)
            r["POST"] = parse_script_output(rm.stdout)["POST"]

    # 2. plan the move
    if variable and a.dry_run:
        pre = parse_num(pre0n)
        out["planned_0n"] = pre + value if vmode == "add" else value
        deltas = {}
        out["basis"] = "unmeasured (variable habit: a real run measures the 0分 change and moves it)"
    elif variable:
        pre, post = parse_num(pre0n), parse_num(r.get("POST0N", ""))
        change = post - pre
        deltas = compute_deltas(r["PRE"], r["POST"])
        if not deltas:
            # Never trust "no change" on the first read: a stalled recalc
            # looks exactly like a habit whose points flow only via 1n+.
            rm = ix_run(build_remeasure_script(target.month, target.day), timeout=90.0)
            if rm.returncode == 0 and not rm.stdout.startswith("ERROR"):
                deltas = compute_deltas(r["PRE"], parse_script_output(rm.stdout)["POST"])
                out["recalc"] = "calculate full (re-measured after no visible change)"
        if not deltas_follow_change(deltas, change):
            revert_or_die(col, r["n_row"], pre0n,
                          "measured 0分 change "
                          f"{ {FEN_NAMES[c]: v for c, v in deltas.items()} } doesn't follow the "
                          f"0n change {fmt_num(pre)} → {fmt_num(post)} for {key}", past_md, src)
        out["basis"] = "measured 0分 delta (variable habit)"
        out["post_0n"] = r.get("POST0N", "")
        if not deltas:
            out["no_daily_points"] = ("no daily 0分 change: this habit's points flow via weekly 1n+, "
                                      "nothing to move")
    elif a.move_only:
        if pre0n == "":
            raise SystemExit(f"ERROR: --move-only but 0n!{col_letter(col)} on {past_md} is empty; "
                             "run without --move-only")
        deltas = {fen_col_for_habit(col): weight * value}
        out["basis"] = "row-2 weight (move-only, no delta measured)"
    elif a.dry_run:
        if already_credited(pre0n):
            out["warning"] = (f"0n cell already = {pre0n}; a real run will report "
                              "'credit already taken' and award nothing (use --move-only to force the move)")
        deltas = {fen_col_for_habit(col): weight * value}
        out["basis"] = "row-2 weight (dry-run estimate)"
    else:
        if already_credited(pre0n):
            # The script skipped the write (guarded in AppleScript). Not an
            # error: the day is already marked, so its points were credited
            # when it was marked. Report and award nothing (2026-09-28).
            out.update({"already_credited": True, "moves": [], "points_awarded": 0,
                        "message": (f"credit already taken: 0n!{col_letter(col)} on {past_md} = {pre0n}; "
                                    "no additional points awarded (--move-only forces the move if "
                                    f"the points still sit on {past_md})")})
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        deltas = compute_deltas(r["PRE"], r["POST"])
        expected = weight * value
        if not deltas_match_expected(deltas, expected, col):
            # A stalled dependency recalc reads as "no change": force a full
            # recalc and measure once more before calling it a mismatch.
            rm = ix_run(build_remeasure_script(target.month, target.day), timeout=90.0)
            if rm.returncode == 0 and not rm.stdout.startswith("ERROR"):
                deltas = compute_deltas(r["PRE"], parse_script_output(rm.stdout)["POST"])
                out["recalc"] = "calculate full (plain calculate showed no matching change)"
        if not deltas_match_expected(deltas, expected, col):
            revert_or_die(col, r["n_row"], pre0n,
                          "measured 0分 change "
                          f"{ {FEN_NAMES[c]: v for c, v in deltas.items()} } != expected "
                          f"{expected} for {key}", past_md, src)
        out["basis"] = "measured 0分 delta"
        out["post_0n"] = r.get("POST0N", "")

    moves = [{"col": col_letter(c), "name": FEN_NAMES[c], "pts": v} for c, v in deltas.items()]
    out["moves"] = moves
    if a.dry_run:
        out["dry_run"] = True
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if in_place or not moves:
        # Target row IS today: the 0n mark already credited today's 0分 by
        # formula. A back-out/credit pair on the same row would be ledger noise.
        # No moves (variable habit with no daily 0分 change): just save.
        if in_place:
            out["in_place"] = True
            out["credit"] = {"date": today_md, "ok": True, "note": "credited by formula, no move"}
        try:
            out["dashboard"] = save_and_refresh()
        except Exception as e:  # noqa: BLE001
            out["dashboard"] = f"ERROR: {e}"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    # 3. relocate through the daemon (ledger-journaled)
    back = neon_excel.batch_append("0分", [(col_letter(c), signed(-v)) for c, v in deltas.items()],
                                   date=past_md, src=src)
    out["back_out"] = {"date": past_md, "ok": bool(back.get("ok")), "row": back.get("row"),
                       "error": back.get("error")}
    if not back.get("ok"):
        out["recovery"] = (
            f"0n is marked but points not moved. Append "
            f"{[(m['col'], signed(-m['pts'])) for m in moves]} to 0分 {past_md} and "
            f"{[(m['col'], signed(m['pts'])) for m in moves]} to 0分 {today_md} "
            f"(src {src!r}) via neon.excel.batch_append" if variable else
            f"0n is marked but points not moved. Rerun: 0n-backfill.py "
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
