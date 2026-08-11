#!/usr/bin/env python3
"""xk887 — weekly family/marriage review.

A full-screen PAGINATED form (prompt_toolkit), same interaction grammar as
0s.py / 1s-survey.py, covering FOUR sheets of xk887.xlsx — one page per
sheet/person: xk88 (marriage/social), xk20 (Theo), xk22 (Ren), xk26 (Rori).
Each page is written to Excel the moment it's submitted (last-field Enter/Tab
or ^S), so later pages can't lose earlier ones. S-Tab from a page's first
field goes back; blanks never clobber, so re-submitting a page is safe.

Rows are keyed by col A's M.W label (Sunday-anchored: M = sunday.month,
W = which Sunday of M), the same convention as Neon's 1分+1s tab. UNLIKE
1分+1s, these sheets do NOT pre-populate future weeks — col A is a formula
chain (`=prev+0.1`, rolling to `=prev+1` at month end) that hardens into a
literal once a row is filled, and the row after the last filled one is
genuinely empty. So a week that doesn't exist yet is APPENDED (new row,
label written as a literal), not just looked up.

xk26's col B (age in weeks) has the same harden-to-literal pattern. If left
blank on a newly appended row, it auto-continues from the previous row's
value + 1 (matching the sheet's own established convention); on an existing
row it's left alone like any other blank.

Usage:
  python3 xk887-survey.py                 # review the last completed Sun-Sat week
  python3 xk887-survey.py 2026-07-15      # review the week containing that date
  python3 xk887-survey.py --from-json F   # non-interactive write from JSON
  python3 xk887-survey.py --print-script --from-json F   # print AppleScript only
"""
from __future__ import annotations

import argparse
import calendar
import copy
import datetime as _dt
import json
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path

IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.sh"
WORKBOOK = "xk887.xlsx"
# ix's OneDrive mount uses the Library/CloudStorage naming convention, not a
# plain ~/OneDrive symlink (confirmed via mdfind on ix, 2026-08-04) — same
# pattern as tools/2s/2s-fast.py's SCORECARD_PATH.
WORKBOOK_PATH = Path.home() / "Library/CloudStorage/OneDrive-Personal/vault-excel" / WORKBOOK
# Recovery dump for answers that fail to write to Excel — a stable cache dir,
# not /tmp (macOS periodic cleanup can reap /tmp; hand-typed answers with no
# other durability shouldn't live there). See write_answers_safely().
RECOVERY_DIR = Path.home() / ".cache/xk887-recovery"

# Each sheet: (sheet name, display title, fields).
# Fields: (key, label, column, kind).  kind: text | textml | num | num_auto
# num_auto = numeric; if blank AND the row is newly appended, auto-continue
# from the previous row's same column + 1 (xk26's age-in-weeks only).
SHEETS = [
    {
        "sheet": "xk88",
        "title": "xk88 · Marriage / Social",
        "fields": [
            ("good",         "Good",                                  "B", "textml"),
            ("regrettable",  "Regrettable",                           "C", "textml"),
            ("focus",        "Focus",                                 "D", "textml"),
            ("notes",        "Notes",                                 "E", "textml"),
            ("upcoming",     "Upcoming",                               "G", "textml"),
            ("did_notes",    "Did / Notes",                           "H", "textml"),
            ("kind",         "What did I do that was kind",           "J", "textml"),
            ("husband",      "Best husband this week?",               "K", "textml"),
        ],
    },
    {
        "sheet": "xk20",
        "title": "xk20 · Theo",
        "fields": [
            ("is",           "He is",                                 "B", "textml"),
            ("new",          "New / Curriculum",                      "C", "textml"),
            ("concerned",    "Concerned / Opportunity",                "D", "textml"),
            ("well",         "Well",                                  "E", "textml"),
            ("better",       "Better",                                "F", "textml"),
            ("notes",        "Notes",                                 "G", "textml"),
            ("goals",        "Goals",                                 "H", "textml"),
        ],
    },
    {
        "sheet": "xk22",
        "title": "xk22 · Ren",
        "fields": [
            ("is",           "She is",                                "B", "textml"),
            ("new",          "New",                                   "C", "textml"),
            ("concerned",    "Concerned",                             "D", "textml"),
            ("well",         "Well",                                  "E", "textml"),
            ("better",       "Better",                                "F", "textml"),
            ("notes",        "Notes",                                 "G", "textml"),
            ("goals",        "Goals",                                 "H", "textml"),
        ],
    },
    {
        "sheet": "xk26",
        "title": "xk26 · Rori",
        "fields": [
            ("age",          "Age (weeks)",                           "B", "num_auto"),
            ("new",          "New",                                   "C", "textml"),
            ("concerned",    "Concerned",                             "D", "textml"),
            ("well",         "Well",                                  "E", "textml"),
            ("better",       "Better",                                "F", "textml"),
            ("notes",        "Notes",                                 "G", "textml"),
            ("goals",        "Goals",                                 "H", "textml"),
        ],
    },
]


_WEEK_LABEL_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})$")


def sunday_for_week_label(label: str, year: int | None = None) -> _dt.date:
    """Reverse of week_row_label: 'M.W' -> that week's Sunday. The label has
    no year component (same as everywhere else this convention is used --
    1分+1s, 0n's 1n+ sheet), so this assumes the current year unless one is
    given. Raises ValueError if W doesn't exist in that month (e.g. '2.6')."""
    m = _WEEK_LABEL_RE.match(label)
    if not m:
        raise ValueError("not a week label: %r" % label)
    month, week = int(m.group(1)), int(m.group(2))
    year = year or _dt.date.today().year
    days_in_month = calendar.monthrange(year, month)[1]
    for day in range(1, days_in_month + 1):
        d = _dt.date(year, month, day)
        if d.weekday() == 6 and (day - 1) // 7 + 1 == week:  # Sunday
            return d
    raise ValueError("no week %s in %d-%02d" % (label, year, month))


def week_range(arg: str | None, today: _dt.date | None = None) -> tuple[_dt.date, _dt.date]:
    """Sun-Sat range of the review week. No arg -> the most recent COMPLETED
    week; an 'M.W' arg (e.g. '7.4') -> that week directly, the same label
    col A is keyed by; any other arg -> an ISO date, the week containing it.
    (Same convention as 1s.)"""
    today = today or _dt.date.today()
    if arg and _WEEK_LABEL_RE.match(arg):
        sunday = sunday_for_week_label(arg)
    elif arg:
        d = _dt.date.fromisoformat(arg)
        sunday = d - _dt.timedelta(days=(d.weekday() + 1) % 7)
    else:
        this_sunday = today - _dt.timedelta(days=(today.weekday() + 1) % 7)
        sunday = this_sunday - _dt.timedelta(days=7)
    return sunday, sunday + _dt.timedelta(days=6)


def week_row_label(sunday: _dt.date) -> str:
    """Col A's M.W label for the week starting at `sunday`, e.g. 2026-07-12 -> '7.2'."""
    return "%d.%d" % (sunday.month, (sunday.day - 1) // 7 + 1)


def _is_num(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _as_applescript(s: str) -> str:
    parts = s.split("\n")
    esc = [p.replace("\\", "\\\\").replace('"', '\\"') for p in parts]
    return " & linefeed & ".join('"%s"' % e for e in esc) if esc else '""'


def field_key(sheet: str, key: str) -> str:
    return "%s_%s" % (sheet, key)


# ---------------------------------------------------------------------------
# Write to xk887.xlsx
# ---------------------------------------------------------------------------

def build_applescript(answers: dict, sunday: _dt.date, sheets=None) -> str:
    """One tell block, one sheet loop per config: the review week's row is
    only ever the sheet's current tail row (col A's M.W label there, read as
    string value NOT value -- the label column is a formula chain with
    float-precision display artifacts, same lesson as 0s.py's date
    matching). The M.W label has no year component, so it is NOT safe to
    scan the whole column for a string match -- that can re-hit an old
    year's row with the same label well before the real tail. If the tail's
    label isn't this week's, append a new row after it. Only non-empty
    answers are written, so blanks never clobber existing cells -- except
    xk26's age, which auto-continues from the previous row on a newly
    appended row only. `sheets` restricts the write to a subset of SHEETS
    (the paginated form writes one sheet per page)."""
    label = week_row_label(sunday)
    L = [
        'tell application "Microsoft Excel"',
        # xk887.xlsx isn't part of any always-open daily-driver Excel
        # session (unlike Neon分v12.2.xlsx) -- it drifts closed between
        # infrequent /xk887 runs. Referencing `workbook "%s"` while it's
        # closed throws, uncaught, all the way up through main() -- confirmed
        # live 2026-08-04: the workbook was closed, the write for the first
        # page of a session crashed the whole process (and reaped its cmux
        # pane) right after the user submitted it, silently losing what
        # they'd typed. `wbNames does not contain` is a plain query, not an
        # error-handler around the reference -- it can't misfire on some
        # OTHER AppleScript error (hung Excel, wrong name) the way a bare
        # try/on-error around the reference would. Same pattern as
        # tools/2s/2s-fast.py's write_scorecard().
        '  set wbNames to (name of every workbook)' ,
        '  if wbNames does not contain "%s" then open POSIX file "%s"' % (WORKBOOK, WORKBOOK_PATH),
        '  set wb to workbook "%s"' % WORKBOOK,
        '  set totalWrote to 0',
        '  set report to ""',
    ]
    for cfg in (sheets if sheets is not None else SHEETS):
        sheet = cfg["sheet"]
        v = sheet  # AppleScript variable prefix; sheet names are valid identifiers
        L += [
            '  set ws to worksheet "%s" of wb' % sheet,
            '  set lastRow_%s to 1' % v,
            '  repeat with r from 2 to 1000',
            '    set av to ""',
            '    try',
            '      set av to (string value of range ("A" & r) of ws)',
            '    end try',
            '    if av is not "" then set lastRow_%s to r' % v,
            '  end repeat',
            '  set tailLabel_%s to ""' % v,
            '  try',
            '    set tailLabel_%s to (string value of range ("A" & lastRow_%s) of ws)' % (v, v),
            '  end try',
            # Only the tail row can legitimately be "this week" -- the M.W
            # label has no year component, so scanning the whole sheet for
            # a string match risks re-hitting an old year's row with the
            # same label (e.g. a prior year's "7.3") well before the real
            # tail, silently overwriting stale history instead of appending.
            '  set isNew_%s to (tailLabel_%s is not "%s")' % (v, v, label),
            '  if isNew_%s then' % v,
            '    set weekRow_%s to lastRow_%s + 1' % (v, v),
            '    set value of range ("A" & weekRow_%s) of ws to %s' % (v, label),
            '  else',
            '    set weekRow_%s to lastRow_%s' % (v, v),
            '  end if',
        ]
        for key, _label, col, kind in cfg["fields"]:
            val = (answers.get(field_key(sheet, key)) or "").strip()
            if kind == "num_auto" and not val:
                L += [
                    '  if isNew_%s then' % v,
                    '    try',
                    '      set prevVal_%s to (value of range ("%s" & lastRow_%s) of ws)' % (v, col, v),
                    '      set value of range ("%s" & weekRow_%s) of ws to (prevVal_%s + 1)' % (col, v, v),
                    '      set totalWrote to totalWrote + 1',
                    '    end try',
                    '  end if',
                ]
                continue
            if not val:
                continue
            if kind in ("num", "num_auto"):
                if not _is_num(val):
                    continue
                L.append('  set value of range ("%s" & weekRow_%s) of ws to %s' % (col, v, float(val)))
            else:
                L.append('  set value of range ("%s" & weekRow_%s) of ws to %s' % (col, v, _as_applescript(val)))
            L.append('  set totalWrote to totalWrote + 1')
        L.append('  set report to report & "%s:" & weekRow_%s & " "' % (sheet, v))
    L += [
        '  save wb',
        '  return "OK: wrote=" & totalWrote & " " & report',
        'end tell',
    ]
    return "\n".join(L)


def write_answers(answers: dict, sunday: _dt.date, sheets=None) -> str:
    script = build_applescript(answers, sunday, sheets=sheets)
    proc = subprocess.run([str(IX_OSA)], input=script, capture_output=True,
                          text=True, timeout=60)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or out.startswith("ERROR"):
        raise RuntimeError(out or proc.stderr.strip() or "ix-osa failed")
    return out


def dump_recovery(answers: dict, sunday: _dt.date, sheet: str | None = None) -> Path:
    """Persist hand-typed answers that failed to reach Excel -- the only
    copy of that data anywhere once the form's own process is gone. Named
    by week + sheet + timestamp so repeated failures never clobber each
    other. Replay via `--from-json <path>`."""
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "-%s" % sheet if sheet else ""
    path = RECOVERY_DIR / ("%s%s-%s.json" % (week_row_label(sunday), suffix, ts))
    path.write_text(json.dumps(answers, ensure_ascii=False, indent=2))
    return path


# ---------------------------------------------------------------------------
# Background writer (2026-08-11): each page's write_answers() call is a
# blocking ix-osa round trip (Excel + AppleScript on ix, sometimes 15s+ when
# the daemon is under load) that used to run between run_page() calls — the
# user stared at "writing N fields..." before the NEXT page's form could even
# render ("the save function takes way too long, should be non-blocking
# while I go on to the next field"). Writes now queue onto a single
# background worker thread and run_paginated() moves on to the next page
# immediately; the worker processes them ONE AT A TIME (never two AppleScript
# writes to the same open workbook concurrently — a real corruption risk, not
# just a style choice) and the results are collected and reported only after
# the whole interactive flow ends, when the terminal is no longer owned by a
# prompt_toolkit full-screen Application (a background thread printing while
# one is active would corrupt the display).
# ---------------------------------------------------------------------------
_write_queue: "queue.Queue" = queue.Queue()
_write_results: list[tuple[str, bool, str, Path | None]] = []
_write_results_lock = threading.Lock()
_writer_thread: threading.Thread | None = None


def _writer_loop() -> None:
    while True:
        item = _write_queue.get()
        if item is None:
            _write_queue.task_done()
            return
        answers, sunday, cfg = item
        try:
            result = write_answers(answers, sunday, sheets=[cfg])
            with _write_results_lock:
                _write_results.append((cfg["sheet"], True, result, None))
        except Exception as e:  # noqa: BLE001
            rec_path = dump_recovery(answers, sunday, sheet=cfg["sheet"])
            with _write_results_lock:
                _write_results.append((cfg["sheet"], False, str(e), rec_path))
        finally:
            _write_queue.task_done()


def queue_write(answers: dict, sunday: _dt.date, cfg: dict) -> None:
    """Non-blocking: snapshot `answers` (later pages mutate the live dict)
    and hand it to the background worker, starting it lazily on first use."""
    global _writer_thread
    if _writer_thread is None:
        _writer_thread = threading.Thread(target=_writer_loop, daemon=True)
        _writer_thread.start()
    _write_queue.put((copy.deepcopy(answers), sunday, cfg))


def drain_writes() -> bool:
    """Block until every queued write has finished, then report results —
    called once, after the interactive multi-page flow ends (normal
    completion OR cancel), never between pages. Must run before the process
    exits: an abrupt exit while ix-osa is mid-write is the corruption risk
    queue_write's serialization elsewhere guards against. Returns True iff
    every queued write succeeded, so the caller can still exit non-zero on a
    background failure even though it was discovered after moving on."""
    if _writer_thread is None:
        return True
    _write_queue.join()
    with _write_results_lock:
        results, _write_results[:] = list(_write_results), []
    all_ok = True
    for sheet, ok, msg, rec_path in results:
        if ok:
            print("xk887 → %s ✓ %s" % (sheet, msg), flush=True)
        else:
            all_ok = False
            print("xk887 → %s ✗ WRITE FAILED: %s" % (sheet, msg), flush=True)
            print("xk887 → answers saved to %s -- replay with --from-json once fixed" % rec_path,
                  flush=True)
    return all_ok


# ---------------------------------------------------------------------------
# Full-screen form (prompt_toolkit) -- same interaction grammar as 0s.py/1s
# ---------------------------------------------------------------------------

def run_page(cfg: dict, sunday: _dt.date, saturday: _dt.date,
             page_no: int, total: int, initial: dict) -> tuple[str | None, dict]:
    """One full-screen page for one sheet/person. Returns (action, answers):
    action 'submit' (write this page, go forward), 'back' (previous page,
    nothing written), or None (cancelled). Fields sit on adjacent lines with
    no blank rows between them — multiline boxes start at one line and grow
    with their content instead of reserving empty height."""
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, ScrollablePane, VSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Frame, TextArea

    areas = {}
    ordered_keys = []
    rows = []
    for key, label, _col, kind in cfg["fields"]:
        fkey = field_key(cfg["sheet"], key)
        ml = kind == "textml"
        area = TextArea(multiline=ml,
                        height=Dimension(min=1, max=6) if ml else 1,
                        wrap_lines=True, style="class:input", scrollbar=False,
                        text=initial.get(fkey, ""))
        areas[fkey] = (area, kind, label)
        ordered_keys.append(fkey)
        tag = " #" if kind in ("num", "num_auto") else ""
        lbl = Window(FormattedTextControl(label + tag), width=32,
                    style="class:label", dont_extend_width=True, wrap_lines=True)
        rows.append(VSplit([lbl, Window(width=1, char=" "), area]))

    msg = {"text": ""}
    nav = "Enter/Tab next · last field saves page" + (" →" if page_no < total else " + finish")
    status = Window(FormattedTextControl(
        lambda: msg["text"] or nav + " · S-Tab back · ^→/^← page · ^S save page · ^Q cancel"),
        height=1, style="class:status")

    root = HSplit([Frame(ScrollablePane(HSplit(rows, padding=0)),
                         title="xk887 %d/%d · %s · week %s (%s–%s)" % (
                             page_no, total, cfg["title"], week_row_label(sunday),
                             sunday.isoformat(), saturday.isoformat())),
                   status])

    ordered = [(k, areas[k][0], areas[k][1]) for k in ordered_keys]
    last_idx = len(ordered) - 1

    def _focused_idx(app):
        cc = app.layout.current_control
        for i, (_k, a, _kind) in enumerate(ordered):
            if a.control is cc:
                return i
        return None

    def _submit(app):
        bad = [lbl for _k, (a, kind, lbl) in areas.items()
               if kind in ("num", "num_auto") and a.text.strip() and not _is_num(a.text.strip())]
        if bad:
            msg["text"] = "Not a number: " + ", ".join(bad)
            return
        app.exit(result="submit")

    kb = KeyBindings()

    @kb.add("tab", eager=True)
    def _(e):
        if _focused_idx(e.app) == last_idx:
            _submit(e.app)
        else:
            e.app.layout.focus_next()

    @kb.add("s-tab", eager=True)
    def _(e):
        if _focused_idx(e.app) == 0 and page_no > 1:
            e.app.exit(result="back")
        else:
            e.app.layout.focus_previous()

    @kb.add("enter", eager=True)
    def _(e):
        idx = _focused_idx(e.app)
        if idx is None:
            return
        kind = ordered[idx][2]
        if kind == "textml":
            e.current_buffer.insert_text("\n")
        elif idx == last_idx:
            _submit(e.app)
        else:
            e.app.layout.focus_next()

    @kb.add("c-s")
    def _(e):
        _submit(e.app)

    # Free page navigation (2026-08-04) -- previously the only way off a page
    # was Tab/Enter at the LAST field (submit, forward) or S-Tab at the
    # FIRST field (back) -- so revisiting an earlier or later page meant
    # tabbing through every field in between. These work from any field on
    # any page. Forward still validates + writes (same as Tab/Enter at the
    # last field); back still writes nothing (blanks never clobber, so a
    # later re-submit of a revisited page is always safe).
    @kb.add("c-right")
    @kb.add("c-pagedown")
    def _(e):
        _submit(e.app)

    @kb.add("c-left")
    @kb.add("c-pageup")
    def _(e):
        if page_no > 1:
            e.app.exit(result="back")

    @kb.add("c-q")
    @kb.add("c-c")
    def _(e):
        e.app.exit(result=None)

    style = Style([
        ("label", "fg:#7c7c7c"),
        ("input", "bg:#1b1b1b fg:#cfcfcf"),
        ("section", "fg:#5f875f bold"),
        ("status", "bg:#111111 fg:#00e676"),
        ("frame.border", "fg:#333333"),
    ])

    app = Application(layout=Layout(root, focused_element=areas[ordered_keys[0]][0]),
                      key_bindings=kb, full_screen=True, style=style,
                      mouse_support=True)
    action = app.run()
    return action, {k: a.text.strip() for k, (a, _kind, _lbl) in areas.items()}


def run_paginated(sunday: _dt.date, saturday: _dt.date) -> int:
    """One page per sheet/person; each page is QUEUED to Excel the moment it
    is submitted and the NEXT page renders immediately — the write itself
    runs in the background (see queue_write/drain_writes) so a crash or
    cancel on page 3 never loses pages 1-2, and typing never blocks on the
    ix-osa round trip. S-Tab from a page's first field goes back
    (already-queued pages can be edited and re-submitted; blanks never
    clobber, so re-writes are safe). Every queued write is waited on and
    reported exactly once, at the very end (drain_writes) — never between
    pages, since a background thread printing while the next page's
    full-screen Application is running would corrupt the display."""
    answers: dict = {}
    total = len(SHEETS)
    queued = []
    i = 0
    while i < total:
        cfg = SHEETS[i]
        action, page = run_page(cfg, sunday, saturday, i + 1, total, answers)
        answers.update(page)
        if action is None:
            drain_writes()
            print("xk887 cancelled on page %d/%d%s." % (
                i + 1, total,
                "; queued before cancel: " + ", ".join(queued) if queued else ""))
            return 1
        if action == "back":
            i -= 1
            continue
        filled = sum(1 for key, _l, _c, _k in cfg["fields"]
                     if (answers.get(field_key(cfg["sheet"], key)) or "").strip())
        print("xk887 → %s: queued %d fields …" % (cfg["sheet"], filled), flush=True)
        queue_write(answers, sunday, cfg)
        if cfg["sheet"] not in queued:
            queued.append(cfg["sheet"])
        i += 1
    all_ok = drain_writes()
    print("xk887 → week %s done (%s)" % (week_row_label(sunday), ", ".join(queued)))
    return 0 if all_ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?",
                    help="'M.W' week label (e.g. 7.4) or any ISO date in the review week "
                         "(default: last completed week)")
    ap.add_argument("--from-json", help="write answers from a JSON file instead of the form")
    ap.add_argument("--print-script", action="store_true", help="print AppleScript, do not write")
    args = ap.parse_args()

    sunday, saturday = week_range(args.date)

    if args.from_json:
        answers = json.loads(Path(args.from_json).read_text())
        if args.print_script:
            print(build_applescript(answers, sunday))
            return 0
        filled = sum(1 for cfg in SHEETS for key, _l, _c, _kind in cfg["fields"]
                     if (answers.get(field_key(cfg["sheet"], key)) or "").strip())
        print("xk887 → writing %d fields to xk887.xlsx week %s …" % (filled, week_row_label(sunday)),
              flush=True)
        try:
            result = write_answers(answers, sunday)
        except Exception as e:  # noqa: BLE001
            print("xk887 ✗ WRITE FAILED: %s" % e, flush=True)
            print("xk887 → input JSON is already durable at %s -- retry once fixed" % args.from_json,
                  flush=True)
            return 1
        print("xk887 → %s (%d fields) · %s" % (week_row_label(sunday), filled, result))
        return 0

    # Interactive: one page per person, written as each page is submitted.
    return run_paginated(sunday, saturday)


if __name__ == "__main__":
    sys.exit(main())
