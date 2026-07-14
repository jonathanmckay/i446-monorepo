#!/usr/bin/env python3
"""0s — daily social/reflection review.

A full-screen form (prompt_toolkit) that shows all questions at once so you can
fill them one after the next, then writes each answer to the matching column of
today's row in the Neon `0s897` tab. The forward-looking "motivation" field is
written to TOMORROW's row instead (it's the motivation you're setting for the
next day).

Column map (0s897 tab, one row per day, date in col B as M/D/YY):
  E Title of the Day        K 霓虹 (num)      P ⌈ (num)   S proud of / others
  F Who did I notice        L 帮助 (num)      Q ⌊ (num)   T learnings / others
  G thankful for            M 身体 (num)      R x̄ (num)   V ⌈ (num)
  H biggest win             N Body Notes                  W ⌊ (num)
  I learning for tomorrow
  D motivation  -> written to TOMORROW's row (forward-looking)

Usage:
  python3 0s.py                 # interactive full-screen form, then writes Excel
  python3 0s.py --from-json F   # non-interactive: write answers from JSON file
  python3 0s.py --print-script  # print the AppleScript for given --from-json, no write
  python3 0s.py [YYYY-MM-DD]    # target a specific day (default: today)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.sh"
WORKBOOK = "Neon分v12.2.xlsx"
SHEET = "0s897"

# (key, label, column, target, kind)   kind: text | textml (multiline) | num
FIELDS = [
    ("title",        "Title of the Day",            "E", "today",    "text"),
    ("notice",       "Who did I notice",            "F", "today",    "text"),
    ("thankful",     "What am I thankful for?",     "G", "today",    "textml"),
    ("win",          "Biggest win today",           "H", "today",    "textml"),
    ("learn",        "Learning for tomorrow",       "I", "today",    "text"),
    ("neon",         "霓虹",                         "K", "today",    "num"),
    ("help",         "帮助",                         "L", "today",    "num"),
    ("body",         "身体",                         "M", "today",    "num"),
    ("bodynotes",    "Body Notes",                  "N", "today",    "textml"),
    ("ceil1",        "⌈  ceiling",                  "P", "today",    "num"),
    ("floor1",       "⌊  floor",                    "Q", "today",    "num"),
    ("mean",         "x̄  mean",                     "R", "today",    "num"),
    ("proud_others", "Proud of (others)",           "S", "today",    "textml"),
    ("learn_others", "Learnings (others)",          "T", "today",    "textml"),
    ("ceil2",        "⌈  ceiling (others)",         "V", "today",    "num"),
    ("floor2",       "⌊  floor (others)",           "W", "today",    "num"),
    ("motivation",   "Motivation (for tomorrow)",   "D", "tomorrow", "text"),
]


def _mdy(d: _dt.date) -> str:
    """Match col B's display format, e.g. 7/14/26 (no leading zeros, 2-digit year)."""
    return "%d/%d/%s" % (d.month, d.day, d.strftime("%y"))


def _is_num(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _as_applescript(s: str) -> str:
    """A quoted AppleScript string expression, preserving newlines via linefeed."""
    parts = s.split("\n")
    esc = [p.replace("\\", "\\\\").replace('"', '\\"') for p in parts]
    return " & linefeed & ".join('"%s"' % e for e in esc) if esc else '""'


def build_applescript(answers: dict, today: _dt.date) -> str:
    """Build the AppleScript that writes filled answers to today's 0s897 row
    (and the motivation field to tomorrow's row). Only non-empty fields are
    written, so blanks never clobber existing cells."""
    today_s = _mdy(today)
    tomorrow_s = _mdy(today + _dt.timedelta(days=1))
    L = [
        'tell application "Microsoft Excel"',
        '  set wb to workbook "%s"' % WORKBOOK,
        '  set ws to worksheet "%s" of wb' % SHEET,
        '  set todayRow to 0',
        '  set tomRow to 0',
        '  repeat with r from 3 to 600',
        '    set bv to (string value of range ("B" & r) of ws)',
        '    if bv = "%s" then set todayRow to r' % today_s,
        '    if bv = "%s" then set tomRow to r' % tomorrow_s,
        '  end repeat',
        '  if todayRow = 0 then return "ERROR: date %s not found in 0s897 col B"' % today_s,
        '  set wrote to 0',
    ]
    for key, _label, col, target, kind in FIELDS:
        val = (answers.get(key) or "").strip()
        if not val:
            continue
        rowvar = "todayRow" if target == "today" else "tomRow"
        guard = target == "tomorrow"
        if guard:
            L.append('  if tomRow > 0 then')
        if kind == "num":
            if not _is_num(val):
                continue
            L.append('  set value of range ("%s" & %s) of ws to %s' % (col, rowvar, float(val)))
        else:
            L.append('  set value of range ("%s" & %s) of ws to %s' % (col, rowvar, _as_applescript(val)))
        L.append('  set wrote to wrote + 1')
        if guard:
            L.append('  end if')
    L += [
        '  save wb',
        '  return "OK: todayRow=" & todayRow & " tomRow=" & tomRow & " wrote=" & wrote',
        'end tell',
    ]
    return "\n".join(L)


def write_answers(answers: dict, today: _dt.date) -> str:
    script = build_applescript(answers, today)
    proc = subprocess.run([str(IX_OSA)], input=script, capture_output=True, text=True, timeout=60)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or out.startswith("ERROR"):
        raise RuntimeError(out or proc.stderr.strip() or "ix-osa failed")
    return out


# ---------------------------------------------------------------------------
# Full-screen form (prompt_toolkit)
# ---------------------------------------------------------------------------
def run_form(today: _dt.date) -> dict | None:
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, ScrollablePane, VSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Frame, TextArea

    areas = {}
    rows = []
    for key, label, _col, _target, kind in FIELDS:
        ml = kind == "textml"
        area = TextArea(multiline=ml, height=(3 if ml else 1), wrap_lines=True,
                        style="class:input", scrollbar=ml)
        areas[key] = (area, kind, label)
        tag = " #" if kind == "num" else ""
        lbl = Window(FormattedTextControl(label + tag), width=24, style="class:label",
                     dont_extend_width=True)
        rows.append(VSplit([lbl, Window(width=1, char=" "), area]))

    msg = {"text": ""}
    status = Window(FormattedTextControl(
        lambda: msg["text"] or "Tab / S-Tab move · ^S save · ^Q cancel   (# = number)"),
        height=1, style="class:status")

    body = HSplit(rows, padding=0)
    root = HSplit([Frame(ScrollablePane(body), title="0s · %s" % _mdy(today)), status])

    kb = KeyBindings()

    @kb.add("tab", eager=True)
    def _(e):
        e.app.layout.focus_next()

    @kb.add("s-tab", eager=True)
    def _(e):
        e.app.layout.focus_previous()

    @kb.add("c-s")
    def _(e):
        bad = [lbl for _k, (a, kind, lbl) in areas.items()
               if kind == "num" and a.text.strip() and not _is_num(a.text.strip())]
        if bad:
            msg["text"] = "Not a number: " + ", ".join(bad)
            return
        e.app.exit(result="submit")

    @kb.add("c-q")
    @kb.add("c-c")
    def _(e):
        e.app.exit(result=None)

    style = Style([
        ("label", "fg:#7c7c7c"),
        ("input", "bg:#1b1b1b fg:#cfcfcf"),
        ("status", "bg:#111111 fg:#00e676"),
        ("frame.border", "fg:#333333"),
    ])

    app = Application(layout=Layout(root, focused_element=areas[FIELDS[0][0]][0]),
                      key_bindings=kb, full_screen=True, style=style, mouse_support=True)
    if app.run() != "submit":
        return None
    return {k: a.text.strip() for k, (a, _kind, _lbl) in areas.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", help="YYYY-MM-DD (default today)")
    ap.add_argument("--from-json", help="write answers from a JSON file instead of the form")
    ap.add_argument("--print-script", action="store_true", help="print AppleScript, do not write")
    args = ap.parse_args()

    today = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()

    if args.from_json:
        answers = json.loads(Path(args.from_json).read_text())
    elif args.print_script:
        answers = {}  # empty demo
    else:
        answers = run_form(today)
        if answers is None:
            print("0s cancelled.")
            return 1

    if args.print_script:
        print(build_applescript(answers, today))
        return 0

    filled = sum(1 for k, *_ in FIELDS if (answers.get(k) or "").strip())
    result = write_answers(answers, today)
    print("0s → %s (%d fields) · %s" % (SHEET, filled, result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
