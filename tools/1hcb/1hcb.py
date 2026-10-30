#!/usr/bin/env python3
"""1hcb — weekly health/body survey, runnable from dtd (`1 hcb` card).

Asks three questions and writes them to the hcbi tab of Neon on the row of
the hcb week's Thursday (the hcb week runs Thu-Wed; every weekly entry since
2026-01-01 sits on a Thursday row):

    1. lowest weight this week   -> BG (Weight)
    2. what did you learn        -> BH (Notes)
    3. goals for this week       -> BJ (Goals (w/hci))

then completes the `1 hcb` habit through the normal /did path. The /1hcb
skill runs the same survey (via --weight/--learned/--goals) before its food
log analysis.

Above the survey it prints a report on the week just ended (the previous
Thursday through yesterday's Wednesday): hcbp vs 45分/day, days moved,
s+hcbp (social) vs 60, calories vs 1800, food groups vs the Daily Dozen,
and the year's hcb ledger (hcbi X384) at the start and end of that week
(2026-10-30).

Usage:
  python3 1hcb.py                    # this hcb week (Thursday on/before today)
  python3 1hcb.py 2026-10-15         # the hcb week containing that date
  python3 1hcb.py --print-context    # show last week's answers, write nothing
  python3 1hcb.py --weight 191.4 --learned "..." --goals "..." [--no-mark]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
from neon import excel  # noqa: E402
from neon.weeks import fiscal_week_label  # noqa: E402
from review_form_writer import run_bounded  # noqa: E402

SHEET = "hcbi"
COLS = {"weight": "BG", "learned": "BH", "goals": "BJ"}
HABIT = "1 hcb"
DID_RUN = Path.home() / "i446-monorepo/tools/did/run.py"
DID_FAST = Path.home() / "i446-monorepo/tools/did/did-fast.py"
CLAIMS = Path.home() / "i446-monorepo/tools/did/agent_claims.py"
DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED = "\033[32m", "\033[31m"

# ── weekly report ────────────────────────────────────────────────────────────
# Targets are the sheet's own: the year ledger (hcbi X384) is, per quarter,
# SUMIF(food,"<>-50") - COUNTIF(food,"<>-50")*86 + SUM(hcbp) -
# COUNTIF(hcbp,"<>0")*45. Food 分 is col X through Q2 and AA from Q3 (row
# 188); -50 is an unlogged day. AA's score penalizes calories over 1800 and
# scores servings out of 21 a day, and protein out of 100g.
FOOD_TARGET, HCBP_TARGET, CAL_CEILING, PROTEIN_TARGET = 86, 45, 1800, 100
SOCIAL_TARGET = 60          # 1n+ s+hcbp weekly target (the /1s reviews' x/60)
FOOD_AA_FROM_ROW = 188
REPORT_LAST_ROW = 400       # day rows end at 369, the ledger rows sit below
# hcbi E..O, Daily Dozen order; the per-group numbers sum to the 21 the score uses.
FOOD_GROUPS = (("bn", "beans", 3), ("br", "berries", 1), ("fr", "fruit", 3),
               ("cr", "cruciferous", 1), ("gr", "grains", 3), ("vg", "vegetables", 2),
               ("fx", "flax", 1), ("g", "greens", 2), ("nt", "nuts", 1),
               ("sp", "spices", 1), ("wtr", "water", 3))
WEEKDAYS = {"M", "T", "W", "Th", "F", "S"}
# Offsets inside a B:AA read_range row.
_B, _C, _E, _P, _U, _X, _Y, _AA = 0, 1, 3, 14, 19, 22, 23, 25
_BULLET = re.compile(r"^\s*[•*\-]\s*")


def week_thursday(d: _dt.date) -> _dt.date:
    """The Thursday that starts the Thu-Wed hcb week containing d."""
    return d - _dt.timedelta(days=(d.weekday() - 3) % 7)


def md(d: _dt.date) -> str:
    return f"{d.month}/{d.day}"


def bullets(text: str) -> str:
    """One '• ' bullet per non-blank line (the sheet's existing style)."""
    lines = [_BULLET.sub("", ln).strip() for ln in (text or "").splitlines()]
    return "\n".join("• " + ln for ln in lines if ln)


def parse_weight(text: str) -> float | None:
    try:
        w = float((text or "").strip())
    except ValueError:
        return None
    return w if 80 <= w <= 400 else None


def _clean(v) -> str:
    s = str(v or "").strip()
    return "" if s == "missing value" else s


def read_week(thursday: _dt.date) -> dict:
    out = {}
    for key, col in COLS.items():
        r = excel.read(SHEET, col, date=md(thursday))
        if not r.get("ok"):
            raise RuntimeError(f"read {SHEET}!{col} {md(thursday)}: {r.get('error')}")
        out[key] = _clean(r.get("value"))
    return out


def write_week(thursday: _dt.date, weight: float, learned: str, goals: str) -> None:
    for key, val in (("weight", f"{weight:g}"), ("learned", learned), ("goals", goals)):
        if key != "weight" and not val:
            continue
        r = excel.write(SHEET, COLS[key], date=md(thursday), value=val, src="1hcb survey")
        if not r.get("ok"):
            raise RuntimeError(f"write {SHEET}!{COLS[key]} {md(thursday)}: {r.get('error')}")


def num(v) -> float | None:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _food_raw(row: list, abs_row: int):
    return row[_AA] if abs_row >= FOOD_AA_FROM_ROW else row[_X]


def ledger_contrib(row: list, abs_row: int) -> float:
    """One day row's share of the year ledger, with Excel's semantics: COUNTIF
    "<>-50" / "<>0" also counts text and blanks, which SUM/SUMIF skip."""
    food, hcbp = _food_raw(row, abs_row), row[_Y]
    t = 0.0
    if num(food) != -50:
        t += (num(food) or 0) - FOOD_TARGET
    if num(hcbp) != 0:
        t += (num(hcbp) or 0) - HCBP_TARGET
    return t


def build_report(rows: list, first_row: int, social: dict | None = None) -> list[str]:
    """Report lines for the 7 day rows starting at rows[0] (sheet row
    `first_row`). `rows` is a B:AA read_range that runs on past the day rows
    to the year-ledger row. `social` maps a 1n+ week label to its s+hcbp."""
    def pad(r):
        return list(r) + [""] * (_AA + 1 - len(r))
    rows = [pad(r) for r in rows]
    week = rows[:7]
    logged_i = [i for i, r in enumerate(week) if num(_food_raw(r, first_row + i)) != -50]
    logged = [week[i] for i in logged_i]
    n = len(logged)
    out = []

    def mark(ok: bool, text: str) -> str:
        return (GREEN if ok else RED) + text + RESET

    hcbp = [num(r[_Y]) or 0 for r in week]
    moved = sum(1 for v in hcbp if v > 0)
    line = (f"hcbp   {mark(sum(hcbp) >= HCBP_TARGET * 7, f'{sum(hcbp):.0f}分')} / "
            f"{HCBP_TARGET * 7} ({HCBP_TARGET}/day) · moved {mark(moved == 7, f'{moved}/7')} days")
    if social:
        line += " · social " + " ".join(
            f"{wk} {mark((num(v) or 0) >= SOCIAL_TARGET, f'{num(v) or 0:g}')}/{SOCIAL_TARGET}"
            for wk, v in social.items())
    out.append(line)

    cals = [num(r[_U]) for r in week if (num(r[_U]) or 0) > 0]
    if cals:
        avg = sum(cals) / len(cals)
        over = sum(1 for c in cals if c > CAL_CEILING)
        prot = [num(r[_P]) or 0 for r in logged]
        pavg = sum(prot) / len(prot) if prot else 0
        out.append(f"cal    avg {mark(avg <= CAL_CEILING, f'{avg:.0f}')} ({len(cals)} days logged) · "
                   f"over {CAL_CEILING} on {mark(over == 0, str(over))} · "
                   f"protein {mark(pavg >= PROTEIN_TARGET, f'{pavg:.0f}g')}/{PROTEIN_TARGET}")
    else:
        out.append("cal    no calories logged")

    if n:
        food = sum(num(_food_raw(week[i], first_row + i)) or 0 for i in logged_i) / n
        avgs = [(code, name, tgt, sum(num(r[_E + k]) or 0 for r in logged) / n)
                for k, (code, name, tgt) in enumerate(FOOD_GROUPS)]
        total = sum(a for *_, a in avgs)
        out.append(f"food   {mark(food >= FOOD_TARGET, f'{food:.0f}分')}/day vs {FOOD_TARGET} · "
                   f"Daily Dozen {mark(total >= 21, f'{total:.1f}')}/21 per day ({n} days)")
        short = sorted((a / tgt, name, a, tgt) for code, name, tgt, a in avgs if a < tgt)
        hit = [name for code, name, tgt, a in avgs if a >= tgt]
        if short:
            out.append("       short: " + ", ".join(f"{nm} {a:.1f}/{t}" for _, nm, a, t in short))
        if hit:
            out.append("       hit:   " + ", ".join(hit))
    else:
        out.append("food   nothing logged")

    day_idx = []
    for i, r in enumerate(rows):
        if str(r[_C]).strip() not in WEEKDAYS:
            break
        day_idx.append(i)
    total = next((num(r[_X]) for r in rows[len(day_idx):]
                  if (num(r[_B]) or 0) >= 2000 and num(r[_X]) is not None), None)
    if total is not None:
        tail = lambda k: sum(ledger_contrib(rows[i], first_row + i) for i in day_idx if i >= k)
        start, end = total - tail(0), total - tail(7)
        out.append(f"ledger {start:,.0f} → {mark(end >= start, f'{end:,.0f}')} "
                   f"({end - start:+,.0f} this week; year now {total:,.0f})")
    else:
        out.append("ledger not found below the day rows")
    return out


def week_report(thu: _dt.date) -> list[str]:
    """Report on the hcb week before `thu` (prev Thursday..Wednesday)."""
    prev, wed = thu - _dt.timedelta(days=7), thu - _dt.timedelta(days=1)
    row = excel.lookup_row(SHEET, md(prev))
    if not row:
        raise RuntimeError(f"no hcbi row for {md(prev)}")
    r = excel.read_range(SHEET, f"B{row}:AA{max(REPORT_LAST_ROW, row + 30)}")
    if not r.get("ok"):
        raise RuntimeError(f"read hcbi B{row}:AA: {r.get('error')}")
    social = {}
    for wk in dict.fromkeys((fiscal_week_label(prev), fiscal_week_label(wed))):
        s = excel.read("1n+", "F", date=wk)
        social[wk] = _clean(s.get("value")) if s.get("ok") else "?"
    head = f"{BOLD}week {prev:%a %-m/%-d} – {wed:%a %-m/%-d}{RESET}"
    return [head] + build_report(r["rows"], row, social)


def mark_done() -> str:
    proc = run_bounded(["/usr/bin/python3", str(DID_RUN), HABIT], timeout=120)
    msg = (proc.stdout or proc.stderr or "").strip().splitlines()
    try:
        run_bounded(["/usr/bin/python3", str(DID_FAST), "--refresh-cache"], timeout=60)
    except Exception:  # noqa: BLE001
        pass
    return msg[-1] if msg else "(no output from did run.py)"


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def ask_multiline(question: str) -> str:
    print(BOLD + question + RESET)
    print(DIM + "(one item per line; blank line to finish)" + RESET)
    lines: list[str] = []
    while True:
        ln = ask("> ")
        if not ln:
            break
        lines.append(ln)
    return "\n".join(lines)


def show(label: str, text: str) -> None:
    if text:
        print(DIM + label + RESET)
        for ln in text.splitlines():
            print(DIM + "  " + ln + RESET)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="weekly 1 hcb survey")
    ap.add_argument("date", nargs="?", help="YYYY-MM-DD: the hcb week containing it")
    ap.add_argument("--print-context", action="store_true", help="show context only")
    ap.add_argument("--weight"), ap.add_argument("--learned"), ap.add_argument("--goals")
    ap.add_argument("--no-mark", action="store_true", help="don't complete the 1 hcb habit")
    args = ap.parse_args(argv)

    thu = week_thursday(_dt.date.fromisoformat(args.date) if args.date else _dt.date.today())
    prev = thu - _dt.timedelta(days=7)
    print(DIM + f"loading hcb week {thu}..{thu + _dt.timedelta(days=6)} from Neon…" + RESET, flush=True)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(3) as pool:
        f_cur, f_last = pool.submit(read_week, thu), pool.submit(read_week, prev)
        f_rep = pool.submit(week_report, thu) if not args.weight else None
        try:
            cur, last = f_cur.result(), f_last.result()
        except Exception as e:  # noqa: BLE001
            print(f"✗ could not read hcbi: {str(e).strip()[:300]}")
            return 1
        try:
            report = f_rep.result() if f_rep else []
        except Exception as e:  # noqa: BLE001 -- the survey still runs
            report = [DIM + f"(report unavailable: {str(e).strip()[:200]})" + RESET]

    print(f"{BOLD}1 hcb · week of {thu:%a %-m/%-d}{RESET}  (hcbi row {md(thu)})")
    if last["weight"]:
        print(DIM + f"last week's lowest weight: {last['weight']}" + RESET)
    show("last week's goals:", last["goals"])
    if report:
        print()
        for ln in report:
            print("  " + ln)
        print()
    if any(cur.values()):
        print(DIM + "already recorded this week:" + RESET)
        for key in COLS:
            if cur[key]:
                print(DIM + f"  {COLS[key]}: {cur[key][:80]}" + RESET)
    if args.print_context:
        return 0

    scripted = args.weight is not None
    if scripted:
        weight = parse_weight(args.weight)
        if weight is None:
            print(f"✗ weight must be a number in pounds: {args.weight!r}")
            return 1
        learned, goals = bullets(args.learned or ""), bullets(args.goals or "")
    else:
        if any(cur.values()) and ask("overwrite this week's answers? [y/N] ").lower() != "y":
            print("nothing written")
            return 2
        while True:
            raw = ask(BOLD + "1. What was your lowest weight this week? " + RESET)
            if not raw:
                print("aborted: no weight, nothing written")
                return 2
            weight = parse_weight(raw)
            if weight is not None:
                break
            print("   a number in pounds, e.g. 191.4")
        learned = bullets(ask_multiline("2. What did you learn from this week?"))
        goals = bullets(ask_multiline("3. Goals for this week?"))

    try:
        write_week(thu, weight, learned, goals)
    except Exception as e:  # noqa: BLE001
        print(f"✗ Excel write failed: {str(e).strip()[:300]}")
        print(f"answers: weight={weight:g}\nlearned:\n{learned}\ngoals:\n{goals}")
        return 1
    written = ", ".join(COLS[k] for k, v in (("weight", 1), ("learned", learned), ("goals", goals)) if v)
    print(f"written to {SHEET} row {md(thu)} ({written})")
    if not args.no_mark:
        print("habit: " + mark_done())
        try:
            run_bounded([sys.executable, str(CLAIMS), "release"], timeout=30)
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
