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
from review_form_writer import run_bounded  # noqa: E402

SHEET = "hcbi"
COLS = {"weight": "BG", "learned": "BH", "goals": "BJ"}
HABIT = "1 hcb"
DID_RUN = Path.home() / "i446-monorepo/tools/did/run.py"
DID_FAST = Path.home() / "i446-monorepo/tools/did/did-fast.py"
CLAIMS = Path.home() / "i446-monorepo/tools/did/agent_claims.py"
DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
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
    try:
        cur, last = read_week(thu), read_week(prev)
    except Exception as e:  # noqa: BLE001
        print(f"✗ could not read hcbi: {str(e).strip()[:300]}")
        return 1

    print(f"{BOLD}1 hcb · week of {thu:%a %-m/%-d}{RESET}  (hcbi row {md(thu)})")
    if last["weight"]:
        print(DIM + f"last week's lowest weight: {last['weight']}" + RESET)
    show("last week's goals:", last["goals"])
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
