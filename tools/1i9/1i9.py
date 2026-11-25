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
  python3 1i9.py --detach        # dtd: save in a detached child, exit at once
  python3 1i9.py --from-json F   # replay an answers file left by --detach
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import os
import re
import sys
import time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
from review_form_writer import run_bounded  # noqa: E402
from neon import excel as neon_excel  # noqa: E402  excel-http daemon client (journaled writes)
import dtd_hide  # noqa: E402  optimistic dtd row-hide for the card --detach closes
import form_detach  # noqa: E402  dtd status line + detached child (--detach)

IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.sh"
WEEK_CALC = Path.home() / ".claude/skills/1i9/week_calc.py"
DID_RUN = Path.home() / "i446-monorepo/tools/did/run.py"
DID_FAST = Path.home() / "i446-monorepo/tools/did/did-fast.py"
AGENT_CLAIMS = Path.home() / "i446-monorepo/tools/did/agent_claims.py"
# --detach keeps its pending answers file here (stable, not /tmp) until the
# sheet write succeeds; the saver child's stdout/stderr go to LOG.
RECOVERY_DIR = Path.home() / ".cache/1i9-recovery"
LOG = Path.home() / ".cache/1i9.log"
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
    """UNUSED since 2026-11-25 (i9_points reads through the daemon, points_raw
    below). Kept only because tools/did/test_dtd_1i9_survey_route.py still
    asserts on it; delete both together."""
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


# ── reads: excel-http daemon only (no raw AppleScript, 2026-11-25) ───────────
# read_range returns cell VALUES as text, where the old `string value` reads
# returned display text: a date cell comes back in AppleScript's long form
# ("Wednesday, November 25, 2026 at 12:00:00\u202fAM") instead of "11/25", and
# a number as "481.0" / "11.299999999999999" instead of "481" / "11.3".
_LONG_DATE_RE = re.compile(r"^[A-Za-z]+, ([A-Za-z]+) (\d{1,2}), (\d{4}) at ")
_MONTH_NUM = {n: i for i, n in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}
_NUM_TEXT_RE = re.compile(r"^-?\d+\.\d+(?:[eE][-+]?\d+)?$")


def _cell_date(text: str) -> _dt.date | None:
    """A real date cell as read_range returns it, else None."""
    m = _LONG_DATE_RE.match(text or "")
    if not m or m.group(1) not in _MONTH_NUM:
        return None
    try:
        return _dt.date(int(m.group(3)), _MONTH_NUM[m.group(1)], int(m.group(2)))
    except ValueError:
        return None


def _general(text: str) -> str:
    """A number value the way a General-format cell shows it ("11.0" → "11",
    "11.299999999999999" → "11.3": week labels are =A(n-1)+0.1). Anything that
    is not a number value passes through untouched."""
    t = (text or "").strip()
    return "%.10g" % float(t) if _NUM_TEXT_RE.match(t) else (text or "")


def _shown_int(text: str) -> str:
    """A points value as the sheet shows it: a whole number, half away from
    zero (分 are integers when presented). 0分!R is General in a narrow column,
    so the old display-text read saw 140.5 as "141" (R79, week of 2026-03-22),
    and parse_points truncates: without this that week reads 1分 low."""
    t = (text or "").strip()
    if not _NUM_TEXT_RE.match(t):
        return text or ""
    return str(int(Decimal(t).quantize(Decimal(1), rounding=ROUND_HALF_UP)))


def points_raw(first: int, last: int) -> str:
    """0分 B (date) and R (i9 points) for rows first..last, in the tab-separated
    "row, M/D, points" lines parse_points reads (what points_script used to
    return from Excel). One daemon read; RuntimeError when it fails."""
    r = neon_excel.read_range(POINTS_SHEET, f"B{first}:R{last}")
    rows = r.get("rows") if r.get("ok") else None
    if not isinstance(rows, list):
        raise RuntimeError(f"{POINTS_SHEET} read failed: {r.get('error') or r}")
    lines, seen_date = [], False
    for i, cells in enumerate(rows, start=first):
        b = str(cells[0]) if cells else ""
        d = _cell_date(b)
        seen_date = seen_date or d is not None
        md = f"{d.month}/{d.day}" if d else b
        pts = _shown_int(str(cells[-1])) if len(cells) == 17 else ""    # B..R is 17 columns
        lines.append(f"{i}\t{md}\t{pts}")
    if not seen_date:
        # Dates that did not come back as dates would sum to a silent 0分.
        raise RuntimeError(f"{POINTS_SHEET} read failed: no date cells in B{first}:B{last}")
    return "\n".join(lines) + "\n"


def i9_points(start: _dt.date, end: _dt.date) -> int:
    """Col R of the 0分 tab, summed over the week's day rows (col B is M/D)."""
    return parse_points(points_raw(1, 400), start, end)


def check_label(row: int, expected: str) -> str:
    """Col A of the i9+m5x2 row, as the sheet shows it (the M.W week label)."""
    # read_range, not read: read() falls back to raw osascript when the daemon is down.
    r = neon_excel.read_range(I9_SHEET, f"A{row}:A{row}")
    rows = r.get("rows") if r.get("ok") else None
    if not rows or not rows[0]:
        raise RuntimeError(f"{I9_SHEET}!A{row} read failed: {r.get('error') or r}")
    return _general(str(rows[0][0])).strip()


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


def mark_done_checked(unhide: bool = False) -> tuple[str, str]:
    """Mark the habit done through run.py, refresh dtd's cache. Returns
    (state, run.py's last line); state is
      "closed"   the Todoist card was closed (or found already closed),
      "rolled"   run.py says the habit is already done this week and left
                 the card alone (it is still open, with a later due),
      "credited" run.py wrote the 1n+ credit but closed no card (none
                 matched, or the Todoist call failed): a second /did would
                 credit the points twice,
      "failed"   anything else (non-zero exit): nothing is confirmed.
    unhide=True (the --detach child): on anything but "closed" the card's
    dtd row is put back BEFORE the cache refresh, so the reload that the
    refresh triggers already shows it. Raises what run_bounded raises."""
    proc = run_bounded(["/usr/bin/python3", str(DID_RUN), HABIT], timeout=120)
    out = (proc.stdout or "") + (proc.stderr or "")
    msg = (proc.stdout or proc.stderr or "").strip().splitlines()
    if proc.returncode == 0 and ("todoist closed" in out or "already closed" in out):
        state = "closed"
    elif proc.returncode == 0 and "already done this week" in out:
        state = "rolled"
    elif proc.returncode == 0 and "✓" in out:
        state = "credited"
    else:
        state = "failed"
    if unhide and state != "closed":
        dtd_hide.unhide_card()
    _refresh_cache()
    return state, (msg[-1] if msg else "(no output from did run.py)")


def _refresh_cache() -> None:
    try:
        run_bounded(["/usr/bin/python3", str(DID_FAST), "--refresh-cache"], timeout=60)
    except Exception:  # noqa: BLE001
        pass


def _unhide() -> None:
    """Put the card's dtd row back and make dtd reload (its watcher reloads
    on the cache refresh; the row was also excluded client-side)."""
    if dtd_hide.unhide_card():
        _refresh_cache()


def stale_pending(min_age: float = 600.0) -> list[Path]:
    """Answers files a --detach child never finished (killed, reboot, failed
    write): older than any save can take. Each is replayable with --from-json."""
    try:
        files = sorted(RECOVERY_DIR.glob("pending-*.json"))
    except OSError:
        return []
    now = time.time()
    out = []
    for f in files:
        try:
            if now - f.stat().st_mtime >= min_age:
                out.append(f)
        except OSError:
            pass
    return out


def mark_done() -> str:
    return mark_done_checked()[1]


def release_claim() -> None:
    try:
        run_bounded([sys.executable, str(AGENT_CLAIMS), "release"], timeout=30)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# --detach (dtd, 2026-11-17): "move saving the 1xk887 survey (and all surveys
# for that matter) to the background so that the UI is more snappy, and then
# we can have the bottom notification letting me know when it is saved."
# Opened inline from dtd the form held the screen through the i9+m5x2 write,
# run.py (1n+ credit + Todoist close), the cache refresh and the claim
# release. With --detach the form process only writes the answers to a file
# in RECOVERY_DIR and starts ONE detached child (this script, --from-json),
# which does the same writes in the same order and reports in dtd's status
# line (lib/form_detach.py). The file is removed only after the sheet write
# succeeded; a failed write leaves it there and names it in the status line.
# ---------------------------------------------------------------------------

def spool_path(start: _dt.date) -> Path:
    return RECOVERY_DIR / ("pending-%s-%d.json" % (start.isoformat(), os.getpid()))


def detach(start: _dt.date, row: int, label: str, rating: str, done: str, exceed: str,
           minutes: int) -> tuple[bool, Path | None]:
    """Hand the save to a detached child. (True, file) = handed off: this
    process must do no write and exit. (False, file or None) = the hand-off
    failed, save inline; a file that could be written is kept until the
    inline write succeeds, so a failed inline write still leaves the answers
    replayable."""
    path = spool_path(start)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"week": start.isoformat(), "row": row, "label": label,
                                    "rating": rating, "done": done, "exceed": exceed,
                                    "minutes": int(minutes)}, ensure_ascii=False, indent=1))
    except OSError:
        return False, None
    # The child always closes the `1 i9` card: hide its dtd row NOW, before
    # the spawn, so the child's unhide can never run ahead of it and the list
    # that comes back never shows the row (lib/dtd_hide.py).
    hid = dtd_hide.hide_card()
    try:
        form_detach.spawn_detached(
            [sys.executable, str(Path(__file__).resolve()),
             "--from-json", str(path), "--unlink-json", "--report-hdr"], LOG)
    except OSError:
        if hid:
            dtd_hide.unhide_card()
        return False, path
    # poke=False: this process still owns the screen (fzf is inside execute()).
    # The child's own line comes from announce(), which waits for us to exit.
    form_detach.hdr("⏳ 1i9 week %s: saving in the background …" % label, poke=False)
    return True, path


def _short(e) -> str:
    return " ".join(str(e).split())[:160] or type(e).__name__


def save_from_json(path: Path, unlink: bool = False, report: bool = False) -> int:
    """(--from-json) Write an answers file left by --detach: the sheet row,
    the habit mark, the claim release, exactly as the inline save. No Toggl
    or sheet read: the form resolved row, label and minutes. `unlink` removes
    the file once the answers are in Excel; `report` (the detached child)
    puts the result in dtd's status line. Any unconfirmed close puts the
    card's dtd row back."""
    def say(msg: str) -> None:
        print(msg, flush=True)
        if report:
            form_detach.announce(msg)

    try:
        data = json.loads(path.read_text())
        row, minutes = int(data["row"]), int(data["minutes"])
        rating, done, exceed = str(data["rating"]), str(data["done"]), str(data.get("exceed") or "")
        if row <= 0 or rating not in RATINGS or not done.strip():
            raise ValueError("no row/rating/answer in it")
        label = str(data.get("label") or "")
    except (OSError, ValueError, KeyError, TypeError) as e:
        _unhide()
        say("✗ 1i9 save FAILED (answers file unreadable: %s) answers in %s" % (_short(e), path))
        return 1
    try:
        write_row(row, rating, done, exceed, minutes, label=label or None)
    except BaseException as e:  # noqa: BLE001  incl. timeouts: the card stays open
        _unhide()
        say("✗ 1i9 week %s save FAILED (%s write: %s) answers in %s"
            % (label or "?", I9_SHEET, _short(e), path))
        return 1
    print("written to " + f"{I9_SHEET}!row {row} (B, D, E, F)", flush=True)
    if unlink:
        try:
            path.unlink()   # the answers are in Excel now
        except OSError:
            pass
    try:
        state, line = mark_done_checked(unhide=True)
    except BaseException as e:  # noqa: BLE001  TimeoutExpired is the usual one
        state, line = "failed", _short(e)
        _unhide()
    line = line.strip()
    print("habit: " + line, flush=True)
    release_claim()
    if state == "closed":
        say("✓ 1i9 week %s saved · %s closed" % (label or "?", HABIT))
        return 0
    # Not closed: mark_done_checked already put the dtd row back.
    where = "%s!row %d" % (I9_SHEET, row)
    if state == "rolled":
        say("✓ 1i9 week %s saved · %s was already done this week, card left alone"
            % (label or "?", HABIT))
        return 0
    if state == "credited":
        # run.py credits 1n+ even when it closes no card: /did would credit twice.
        say("✗ 1i9 week %s saved (%s) and credited, but the %s card is NOT closed: "
            "close it in Todoist by hand, do not /did it again" % (label or "?", where, HABIT))
        return 1
    say("✗ 1i9 week %s saved (%s), but marking %s done FAILED (%s): "
        "check 1n+ before running /did %s" % (label or "?", where, HABIT, line, HABIT))
    return 1


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
    ap.add_argument("--detach", action="store_true",
                    help="after the form, save in a detached child and exit at once (dtd)")
    ap.add_argument("--from-json", help="write an answers file left by --detach (no reads)")
    ap.add_argument("--unlink-json", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--report-hdr", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.from_json:
        return save_from_json(Path(args.from_json), unlink=args.unlink_json,
                              report=args.report_hdr)

    start, end, row, label = resolve_week(args.date, _dt.date.today())
    for f in stale_pending():
        print(f"⚠ unsaved 1i9 answers from an earlier run: {f} (replay: 1i9.py --from-json {f})")
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
    spool = None
    if args.detach:
        handed, spool = detach(start, row, sheet_label or label, rating, done, exceed, minutes)
        if handed:
            print(f"1i9 → saving in the background (log: {LOG})")
            return 0
        print("1i9 → could not start the background save; saving here")
    try:
        write_row(row, rating, done, exceed, minutes, label=sheet_label or label)
    except Exception as e:  # noqa: BLE001
        print(f"✗ Excel write failed: {str(e).strip()[:300]}")
        if spool is not None:
            print(f"answers kept in {spool}; replay with: 1i9.py --from-json {spool}")
        else:
            print("answers were not saved; rerun /1-i9 to try again")
        return 1
    if spool is not None:
        try:
            spool.unlink()
        except OSError:
            pass
    print("written to " + f"{I9_SHEET}!row {row} (B, D, E, F)")
    print("habit: " + mark_done())
    release_claim()
    return 0


if __name__ == "__main__":
    sys.exit(main())
