#!/usr/bin/env python3
"""1i9 — weekly i9 (Microsoft/Xbox) review, runnable from dtd (`1 i9` card).

Terminal version of ~/.claude/skills/1i9/SKILL.md: resolves the last completed
Sun-Sat week, shows its i9 minutes (Toggl project i9) and i9 points (0分 col R),
asks the three survey questions, writes B/D/E/F of the week's row in the Neon
`i9+m5x2` sheet (C is a formula; never touched), then completes the `1 i9`
habit through the normal /did path.

Usage:
  python3 1i9.py                 # most recent completed week
  python3 1i9.py 2026-10-04      # the week containing that date
  python3 1i9.py --print-context # show the numbers, write nothing
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
from review_form_writer import run_bounded  # noqa: E402
from neon import excel as neon_excel  # noqa: E402  excel-http daemon client (journaled writes)

IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.sh"
WEEK_CALC = Path.home() / ".claude/skills/1i9/week_calc.py"
DID_RUN = Path.home() / "i446-monorepo/tools/did/run.py"
DID_FAST = Path.home() / "i446-monorepo/tools/did/did-fast.py"
CLAUDE_JSON = Path.home() / ".claude.json"
WORKBOOK = "Neon分v12.2.xlsx"
I9_SHEET = "i9+m5x2"
POINTS_SHEET = "0分"
I9_PROJECT_ID = 209635316
HABIT = "1 i9"
RATINGS = {"MM": "missed", "MA": "met ambition", "EE": "exceeded", "OL": "out (sick/leave)"}
DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def _load_week_calc():
    spec = importlib.util.spec_from_file_location("week_calc_1i9", WEEK_CALC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def resolve_week(arg: str | None, today: _dt.date) -> tuple[_dt.date, _dt.date, int, str]:
    """(week_start, week_end, sheet row, expected M.W label)."""
    wc = _load_week_calc()
    if arg:
        d = _dt.date.fromisoformat(arg)
        start = d - _dt.timedelta(days=(d.weekday() + 1) % 7)  # Sunday on/before d
        end = start + _dt.timedelta(days=6)
    else:
        start, end = wc.last_completed_week(today)
    num = wc.fiscal_week_num(start)
    return start, end, num + 2, wc.label_for_fiscal_week(num)


def _toggl_key() -> str:
    cj = json.loads(CLAUDE_JSON.read_text())
    return cj["mcpServers"]["toggl_server"]["env"]["TOGGL_API_KEY"]


def i9_minutes(start: _dt.date, end: _dt.date) -> int:
    """Minutes on the i9 Toggl project for Sun-Sat, local-tz boundaries."""
    # Shared client (throttle, per-bucket cooldown, request log) instead of a
    # raw GET (2026-10-20). get_entries sends local-midnight bounds; the
    # Sun-Sat local-day check below stays as belt and braces.
    import os
    os.environ.setdefault("TOGGL_API_KEY", _toggl_key())
    root = str(Path.home() / "i446-monorepo")
    if root not in sys.path:
        sys.path.insert(0, root)
    from mcp.toggl_server import toggl_api  # noqa: E402
    raw = toggl_api.get_entries(start.isoformat(), (end + _dt.timedelta(days=1)).isoformat(),
                                include_running=False) or []
    entries = []
    for e in raw:
        try:
            d = _dt.datetime.fromisoformat(
                str(e["start"]).replace("Z", "+00:00")).astimezone().date()
        except (KeyError, ValueError):
            continue
        if start <= d <= end:
            entries.append(e)
    return sum(int(e.get("duration", 0)) // 60 for e in entries
               if e.get("project_id") == I9_PROJECT_ID and int(e.get("duration", 0)) > 0)


def _osa(script: str, timeout: int = 120) -> str:
    proc = run_bounded([str(IX_OSA)], input=script, timeout=timeout)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or out.startswith("ERROR"):
        raise RuntimeError(out or proc.stderr.strip() or "ix-osa failed")
    return out


def points_script(first: int, last: int) -> str:
    return "\n".join([
        'tell application "Microsoft Excel"',
        f'  set wb to workbook "{WORKBOOK}"',
        f'  set s to sheet "{POINTS_SHEET}" of wb',
        '  set out to ""',
        '  set sep to ASCII character 9  -- the `tab` constant does not expand on this path',
        f'  repeat with i from {first} to {last}',
        '    set out to out & i & sep & (string value of range ("B" & i) of s) & sep & (string value of range ("R" & i) of s) & linefeed',
        '  end repeat',
        '  return out',
        'end tell',
    ])


_MD_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})\s*$")


def parse_points(raw: str, start: _dt.date, end: _dt.date) -> int:
    """Sum col R over rows whose col B is an M/D date inside the week."""
    days = {(start + _dt.timedelta(days=k)).strftime("%-m/%-d") for k in range(7)}
    total = 0
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not _MD_RE.match(parts[1] or ""):
            continue
        if parts[1].strip() in days:
            try:
                total += int(float(parts[2] or 0))
            except ValueError:
                pass
    return total


def i9_points(start: _dt.date, end: _dt.date) -> int:
    """Col R of the 0分 tab, summed over the week's day rows (col B is M/D)."""
    raw = _osa(points_script(1, 400))
    return parse_points(raw, start, end)


def check_label(row: int, expected: str) -> str:
    out = _osa('tell application "Microsoft Excel"\n  set wb to workbook "%s"\n'
               '  return string value of cell 1 of row %d of sheet "%s" of wb\nend tell'
               % (WORKBOOK, row, I9_SHEET))
    return out.strip()


def _as_applescript(s: str) -> str:
    esc = [p.replace("\\", "\\\\").replace('"', '\\"') for p in s.split("\n")]
    return " & linefeed & ".join('"%s"' % e for e in esc) if esc else '""'


def write_script(row: int, rating: str, done: str, exceed: str, minutes: int) -> str:
    return "\n".join([
        'tell application "Microsoft Excel"',
        f'  set wb to workbook "{WORKBOOK}"',
        f'  set s to sheet "{I9_SHEET}" of wb',
        f'  set value of cell 2 of row {row} of s to "{rating}"',
        f'  set value of cell 4 of row {row} of s to {_as_applescript(done)}',
        f'  set value of cell 5 of row {row} of s to {_as_applescript(exceed)}',
        f'  set value of cell 6 of row {row} of s to {int(minutes)}',
        '  save wb',
        f'  return "OK: {I9_SHEET}!row {row}"',
        'end tell',
    ])


def daemon_items(rating: str, done: str, exceed: str, minutes: int) -> list[dict]:
    """B/D/E as prose (mode "text": never a formula, newlines kept), F a number."""
    return [{"col": "B", "value": rating, "mode": "text"},
            {"col": "D", "value": done, "mode": "text"},
            {"col": "E", "value": exceed, "mode": "text"},
            {"col": "F", "value": int(minutes)}]


def write_row(row: int, rating: str, done: str, exceed: str, minutes: int,
              label: str | None = None) -> str:
    # Daemon first (journaled, row-anchored with the week label); the raw
    # ix-osa script is the daemon-down fallback only (Neon-off-Excel step 2c
    # deferred item, 2026-10-30). The row was verified against col A already.
    try:
        r = neon_excel.batch_write(I9_SHEET, daemon_items(rating, done, exceed, minutes),
                                   row=row, date=label, src="1i9", fallback=False)
        if r.get("ok"):
            return f"OK: {I9_SHEET}!row {row}"
        if not r.get("fallback"):
            raise RuntimeError(f"{I9_SHEET} write failed: {r.get('error') or r}")
    except RuntimeError:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"1i9 daemon path failed ({e}); using ix-osa", file=sys.stderr)
    return _osa(write_script(row, rating, done, exceed, minutes), timeout=120)


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


def ask_rating() -> str:
    print(BOLD + "1. Rating vs. expectations?" + RESET)
    for k, v in RATINGS.items():
        print(f"   {k} = {v}")
    while True:
        r = ask("> ").upper()
        if r in RATINGS:
            return r
        if not r:
            raise SystemExit("aborted: no rating")
        print("   enter MM, MA, EE, or OL")


def ask_multiline(question: str) -> str:
    print(BOLD + question + RESET)
    print(DIM + "(one item per line; blank line to finish)" + RESET)
    lines: list[str] = []
    while True:
        ln = ask("> ")
        if not ln:
            break
        lines.append(ln)
    return "\n".join(lines).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="weekly 1 i9 review")
    ap.add_argument("date", nargs="?", help="YYYY-MM-DD: review the week containing it")
    ap.add_argument("--print-context", action="store_true", help="show numbers only")
    args = ap.parse_args(argv)

    start, end, row, label = resolve_week(args.date, _dt.date.today())
    print(DIM + f"loading i9 week {start}..{end} from Toggl and Ix…" + RESET, flush=True)
    try:
        minutes = i9_minutes(start, end)
        points = i9_points(start, end)
        sheet_label = check_label(row, label)
    except Exception as e:  # noqa: BLE001
        print(f"✗ could not read the week: {str(e).strip()[:300]}")
        return 1

    print(f"i9 week {sheet_label} ({start}–{end}, sheet row {row})")
    print(f"Minutes: {minutes}m ({minutes // 60}h {minutes % 60}m)")
    print(f"Points: {points}分")
    if args.print_context:
        return 0
    if label and sheet_label != label:
        print(f"✗ sheet row {row} is labelled {sheet_label!r}, expected {label!r}. Nothing written.")
        return 1

    rating = ask_rating()
    done = ask_multiline("2. What did I get done last week?")
    exceed = ask_multiline("3. What does exceeding expectations this week look like?")
    if not done:
        print("aborted: no 'got done' answer, nothing written")
        return 1
    try:
        write_row(row, rating, done, exceed, minutes, label=sheet_label or label)
    except Exception as e:  # noqa: BLE001
        print(f"✗ Excel write failed: {str(e).strip()[:300]}")
        print("answers were not saved; rerun /1-i9 to try again")
        return 1
    print("written to " + f"{I9_SHEET}!row {row} (B, D, E, F)")
    print("habit: " + mark_done())
    try:
        run_bounded([sys.executable, str(Path.home() / "i446-monorepo/tools/did/agent_claims.py"), "release"], timeout=30)
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
