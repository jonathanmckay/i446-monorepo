#!/usr/bin/env python3
"""0s — daily social/reflection review.

A full-screen form (prompt_toolkit) that shows all questions at once so you can
fill them one after the next, then writes each answer to the matching column of
the REVIEWED DAY's row in the Neon `0s897` tab. 0s is accrual/retrospective: you
fill it the next day about the day before, so the reviewed day defaults to
YESTERDAY. The forward-looking "motivation" field is written to the FOLLOWING
day's row (today, by default) — it's the motivation you're setting for the day
after the one you're reviewing.

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
  python3 0s.py [YYYY-MM-DD]    # review a specific day (default: yesterday)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.sh"
DID_FAST = Path.home() / "i446-monorepo/tools/did/did-fast.py"
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
    ("motivation",   "Motivation (for today)",      "D", "tomorrow", "text"),
    # Not a neon column (col=None → never written to Excel). 1 marks 0l done via
    # did-fast; 0 or blank leaves it. Kept last so Enter/Tab on it saves.
    ("points_checked", "Points checked? (1 = mark 0l done)", None, None, "num"),
]


def _mdy(d: _dt.date) -> str:
    """Match col B's display format, e.g. 7/14/26 (no leading zeros, 2-digit year)."""
    return "%d/%d/%s" % (d.month, d.day, d.strftime("%y"))


def _review_date(arg: str | None) -> _dt.date:
    """The day the survey is ABOUT. 0s is accrual/retrospective — filled the next
    day about the day before — so with no arg it defaults to YESTERDAY. The main
    fields land in this row; the forward-looking motivation lands in the next
    day's row (this date + 1)."""
    if arg:
        return _dt.date.fromisoformat(arg)
    return _dt.date.today() - _dt.timedelta(days=1)


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


def build_applescript(answers: dict, target: _dt.date) -> str:
    """Build the AppleScript that writes filled answers to the reviewed day's
    0s897 row (`target`), and the forward-looking motivation to the following
    day's row (`target + 1`). Only non-empty fields are written, so blanks never
    clobber existing cells."""
    target_s = _mdy(target)
    next_s = _mdy(target + _dt.timedelta(days=1))
    L = [
        'tell application "Microsoft Excel"',
        '  set wb to workbook "%s"' % WORKBOOK,
        '  set ws to worksheet "%s" of wb' % SHEET,
        '  set todayRow to 0',
        '  set tomRow to 0',
        '  repeat with r from 3 to 600',
        '    set bv to (string value of range ("B" & r) of ws)',
        '    if bv = "%s" then set todayRow to r' % target_s,
        '    if bv = "%s" then set tomRow to r' % next_s,
        '  end repeat',
        '  if todayRow = 0 then return "ERROR: date %s not found in 0s897 col B"' % target_s,
        '  set wrote to 0',
    ]
    for key, _label, col, target, kind in FIELDS:
        val = (answers.get(key) or "").strip()
        if not val or not col:          # col=None → non-neon field (e.g. points_checked)
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


def write_answers(answers: dict, target: _dt.date) -> str:
    script = build_applescript(answers, target)
    proc = subprocess.run([str(IX_OSA)], input=script, capture_output=True, text=True, timeout=60)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or out.startswith("ERROR"):
        raise RuntimeError(out or proc.stderr.strip() or "ix-osa failed")
    return out


# ---------------------------------------------------------------------------
# Full-screen form (prompt_toolkit)
# ---------------------------------------------------------------------------
def run_form(target: _dt.date) -> dict | None:
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
        lambda: msg["text"] or "Enter/Tab next · Enter or Tab on last field saves · S-Tab back · ^S save · ^Q cancel"),
        height=1, style="class:status")

    body = HSplit(rows, padding=0)
    root = HSplit([Frame(ScrollablePane(body), title="0s · reviewing %s" % _mdy(target)), status])

    ordered = [(k, areas[k][0], areas[k][1]) for k, *_ in FIELDS]  # (key, area, kind) in field order
    last_idx = len(ordered) - 1

    def _focused_idx(app):
        cc = app.layout.current_control
        for i, (_k, a, _kind) in enumerate(ordered):
            if a.control is cc:
                return i
        return None

    def _submit(app):
        bad = [lbl for _k, (a, kind, lbl) in areas.items()
               if kind == "num" and a.text.strip() and not _is_num(a.text.strip())]
        if bad:
            msg["text"] = "Not a number: " + ", ".join(bad)
            return
        app.exit(result="submit")

    kb = KeyBindings()

    @kb.add("tab", eager=True)
    def _(e):
        # Tab on the last field saves + exits; elsewhere it advances.
        if _focused_idx(e.app) == last_idx:
            _submit(e.app)
        else:
            e.app.layout.focus_next()

    @kb.add("s-tab", eager=True)
    def _(e):
        e.app.layout.focus_previous()

    @kb.add("enter", eager=True)
    def _(e):
        idx = _focused_idx(e.app)
        if idx is None:
            return
        kind = ordered[idx][2]
        if kind == "textml":
            e.current_buffer.insert_text("\n")   # multiline fields: newline
        elif idx == last_idx:
            _submit(e.app)                        # last field: save + exit
        else:
            e.app.layout.focus_next()             # single-line: next field

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

    target = _review_date(args.date)  # default: yesterday (accrual/retrospective)

    if args.from_json:
        answers = json.loads(Path(args.from_json).read_text())
    elif args.print_script:
        answers = {}  # empty demo
    else:
        answers = run_form(target)
        if answers is None:
            print("0s cancelled.")
            return 1

    if args.print_script:
        print(build_applescript(answers, target))
        return 0

    filled = sum(1 for k, _l, col, *_ in FIELDS if col and (answers.get(k) or "").strip())
    # The Excel write runs over ssh to Ix and can take many seconds; after the
    # full-screen form restores the shell, silence here reads as a frozen
    # terminal (user report 2026-07-25). Say what's happening, immediately.
    print("0s → writing %d fields to Neon %s (%s) …" % (filled, SHEET, target),
          flush=True)
    result = write_answers(answers, target)
    msg = "0s → %s (%d fields) · %s" % (SHEET, filled, result)

    # "Points checked" is not a neon column: 1 marks 0l done via did-fast;
    # 0/blank leaves it alone.
    if (answers.get("points_checked") or "").strip() == "1":
        print("0s → marking 0l done …", flush=True)
        try:
            # 0l's did-fast path does two sequential Excel round-trips (0n
            # write + 0l-completion-time write, ~30s+15s budget) plus a
            # Todoist search/close — 60s left too little margin and was
            # timing out (2026-08-04 bug report: 0s wrote to Neon but 0l
            # silently stayed unmarked).
            proc = subprocess.run(["/usr/bin/python3", str(DID_FAST), "0l"],
                                   capture_output=True, text=True, timeout=120)
            # did-fast always exits 0 and reports per-write ok/error in its
            # JSON stdout — a clean subprocess return does NOT mean the 0n
            # write actually succeeded, so it must be checked explicitly
            # rather than assumed from the absence of an exception.
            wrote_ok = False
            try:
                on_write = json.loads(proc.stdout).get("0n_write") or {}
                wrote_ok = bool(on_write.get("ok"))
                fail_reason = on_write.get("error")
            except (json.JSONDecodeError, AttributeError):
                fail_reason = (proc.stderr or proc.stdout or "unknown").strip()[:200]
            if wrote_ok:
                msg += " · 0l marked done"
            else:
                msg += " · 0l mark FAILED: %s" % fail_reason
        except Exception as e:  # noqa: BLE001
            msg += " · 0l mark FAILED: %s" % e

    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
