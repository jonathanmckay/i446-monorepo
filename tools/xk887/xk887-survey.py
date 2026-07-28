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
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.sh"
WORKBOOK = "xk887.xlsx"

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


def week_range(arg: str | None, today: _dt.date | None = None) -> tuple[_dt.date, _dt.date]:
    """Sun-Sat range of the review week. No arg -> the most recent COMPLETED
    week; a date arg -> the week containing it. (Same convention as 1s.)"""
    today = today or _dt.date.today()
    if arg:
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
    """One tell block, one sheet loop per config: find the review week's row
    by col A's M.W label (string value, NOT value -- the label column is a
    formula chain with float-precision display artifacts, same lesson as
    0s.py's date matching); if the week isn't there yet, append a new row.
    Only non-empty answers are written, so blanks never clobber existing
    cells -- except xk26's age, which auto-continues from the previous row
    on a newly appended row only. `sheets` restricts the write to a subset
    of SHEETS (the paginated form writes one sheet per page)."""
    label = week_row_label(sunday)
    L = [
        'tell application "Microsoft Excel"',
        '  set wb to workbook "%s"' % WORKBOOK,
        '  set totalWrote to 0',
        '  set report to ""',
    ]
    for cfg in (sheets if sheets is not None else SHEETS):
        sheet = cfg["sheet"]
        v = sheet  # AppleScript variable prefix; sheet names are valid identifiers
        L += [
            '  set ws to worksheet "%s" of wb' % sheet,
            '  set weekRow_%s to 0' % v,
            '  set lastRow_%s to 1' % v,
            '  repeat with r from 2 to 1000',
            '    set av to ""',
            '    try',
            '      set av to (string value of range ("A" & r) of ws)',
            '    end try',
            '    if av is not "" then',
            '      set lastRow_%s to r' % v,
            '      if av = "%s" then set weekRow_%s to r' % (label, v),
            '    end if',
            '  end repeat',
            '  set isNew_%s to (weekRow_%s = 0)' % (v, v),
            '  if isNew_%s then' % v,
            '    set weekRow_%s to lastRow_%s + 1' % (v, v),
            '    set value of range ("A" & weekRow_%s) of ws to %s' % (v, label),
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
        lambda: msg["text"] or nav + " · S-Tab back · ^S save page · ^Q cancel"),
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
    """One page per sheet/person; each page is WRITTEN to Excel as soon as it
    is submitted, so a crash or cancel on page 3 never loses pages 1-2.
    S-Tab from a page's first field goes back (already-written pages can be
    edited and re-submitted; blanks never clobber, so re-writes are safe)."""
    answers: dict = {}
    total = len(SHEETS)
    written = []
    i = 0
    while i < total:
        cfg = SHEETS[i]
        action, page = run_page(cfg, sunday, saturday, i + 1, total, answers)
        answers.update(page)
        if action is None:
            print("xk887 cancelled on page %d/%d%s." % (
                i + 1, total,
                "; already written: " + ", ".join(written) if written else ""))
            return 1
        if action == "back":
            i -= 1
            continue
        filled = sum(1 for key, _l, _c, _k in cfg["fields"]
                     if (answers.get(field_key(cfg["sheet"], key)) or "").strip())
        print("xk887 → %s: writing %d fields …" % (cfg["sheet"], filled), flush=True)
        result = write_answers(answers, sunday, sheets=[cfg])
        print("xk887 → %s ✓ %s" % (cfg["sheet"], result), flush=True)
        if cfg["sheet"] not in written:
            written.append(cfg["sheet"])
        i += 1
    print("xk887 → week %s done (%s)" % (week_row_label(sunday), ", ".join(written)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", help="any date in the review week (default: last completed week)")
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
        result = write_answers(answers, sunday)
        print("xk887 → %s (%d fields) · %s" % (week_row_label(sunday), filled, result))
        return 0

    # Interactive: one page per person, written as each page is submitted.
    return run_paginated(sunday, saturday)


if __name__ == "__main__":
    sys.exit(main())
