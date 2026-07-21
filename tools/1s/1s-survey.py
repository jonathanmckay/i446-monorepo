#!/usr/bin/env python3
"""1s-survey — weekly strategic review survey (the manual questions of the
Neon `1分+1s` tab), as a full-screen form like 0s.py.

Above each question the form surfaces the week's DAILY answers to the
analogous 0s897 question (titles, wins, learnings, proud/regret w/others,
⌈/⌊/x̄), so answering is selecting/condensing rather than composing de novo:
type a day digit (1-7, comma-lists ok: "2,5") as the whole answer and it
expands to that day's text on save. The High/Low/Avg fields come prefilled
from the week's daily ⌈/⌊/x̄ (max/min/mean); edit or accept.

Week rows in `1分+1s` are keyed by col A's M.W label (Sunday-anchored:
M = sunday.month, W = which Sunday of M), e.g. Sun 7/12 → 7.2.

Usage:
  python3 1s-survey.py                # review the last completed Sun-Sat week
  python3 1s-survey.py 2026-07-15     # review the week containing that date
  python3 1s-survey.py --from-json F  # non-interactive write (tests/scripting)
  python3 1s-survey.py --print-script --from-json F   # print AppleScript only
  python3 1s-survey.py --print-context                # dump fetched context
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
SHEET = "1分+1s"
DAILY_SHEET = "0s897"

# (key, label, column in 1分+1s, kind, context_key)
# kind: text | textml | num. context_key names the daily-answer list shown
# above the field (None = no daily analog).
#
# Rating (P), High (W), Low (X), Avg (Y) are DELIBERATELY absent: those cells
# hold live formulas (P pulls 'i9'!B{row}; W/X/Y are MAX/MIN/AVERAGE over the
# week's 0s897 ⌈/⌊/x̄ rows) — they compute themselves from the daily surveys,
# and a form write would clobber the formulas (user decision 2026-07-21).
FIELDS = [
    ("title",         "Title for the Week",         "R",  "text",   "titles"),
    ("win",           "Biggest Win",                "S",  "textml", "wins"),
    ("missed",        "Biggest missed opportunity", "T",  "textml", "learnings"),
    ("proud_others",  "Proud of w/others",          "U",  "textml", "proud"),
    ("regret_others", "Regret w/others",            "V",  "textml", "learn_others"),
    ("notes",         "Notes",                      "AO", "textml", "learnings"),
]

# 0s897 columns backing each context list (see tools/0s/0s.py FIELDS).
DAILY_COLS = {"titles": "E", "wins": "H", "learnings": "I", "proud": "S",
              "learn_others": "T", "ceil": "P", "floor": "Q", "mean": "R"}

DAY_ABBR = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]


def week_range(arg: str | None, today: _dt.date | None = None) -> tuple[_dt.date, _dt.date]:
    """Sun-Sat range of the review week. No arg → the most recent COMPLETED
    week; a date arg → the week containing it."""
    today = today or _dt.date.today()
    if arg:
        d = _dt.date.fromisoformat(arg)
        sunday = d - _dt.timedelta(days=(d.weekday() + 1) % 7)
    else:
        this_sunday = today - _dt.timedelta(days=(today.weekday() + 1) % 7)
        sunday = this_sunday - _dt.timedelta(days=7)
    return sunday, sunday + _dt.timedelta(days=6)


def week_row_label(sunday: _dt.date) -> str:
    """Col A's M.W label for the week starting at `sunday` (which Sunday of
    the month it is), e.g. 2026-07-12 → '7.2'."""
    return "%d.%d" % (sunday.month, (sunday.day - 1) // 7 + 1)


def _mdy(d: _dt.date) -> str:
    return "%d/%d/%s" % (d.month, d.day, d.strftime("%y"))


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


# ---------------------------------------------------------------------------
# Context fetch: the week's daily 0s897 answers
# ---------------------------------------------------------------------------

def build_context_script(dates: list[_dt.date]) -> str:
    """One ix-osa round trip: find each date's 0s897 row (bulk col-B read),
    then bulk-read E:T per found row. Field/row markers keep multiline
    answers parseable."""
    conds = "\n".join(
        '    if bv = "%s" then set end of hits to {%d, r}' % (_mdy(d), i)
        for i, d in enumerate(dates))
    return f'''tell application "Microsoft Excel"
  set ws to worksheet "{DAILY_SHEET}" of workbook "{WORKBOOK}"
  -- string value (displayed M/D/YY), NOT value: col B holds real date cells,
  -- and a date's `as text` is the long form that never matches M/D/YY.
  set tmpB to string value of range "B3:B600" of ws
  set hits to {{}}
  repeat with r from 3 to 600
    set bv to ""
    try
      set bv to (item 1 of (item (r - 2) of tmpB)) as text
    end try
{conds}
  end repeat
  set out to ""
  repeat with h in hits
    set di to item 1 of h
    set rr to item 2 of h
    set tmpR to value of range ("E" & rr & ":T" & rr) of ws
    set rowVals to item 1 of tmpR
    set out to out & "<<ROW " & di & ">>"
    repeat with v in rowVals
      set vv to ""
      try
        set vv to v as text
      end try
      set out to out & "<<F>>" & vv
    end repeat
  end repeat
  return out
end tell'''


def parse_context(raw: str, dates: list[_dt.date]) -> dict[str, list[tuple[int, str]]]:
    """→ {context_key: [(day_index, text), ...]} with blanks dropped.
    E:T is 16 cells; offsets: E=0 H=3 I=4 P=11 Q=12 R=13 S=14 T=15."""
    off = {"titles": 0, "wins": 3, "learnings": 4, "ceil": 11, "floor": 12,
           "mean": 13, "proud": 14, "learn_others": 15}
    ctx: dict[str, list[tuple[int, str]]] = {k: [] for k in off}
    for chunk in raw.split("<<ROW ")[1:]:
        head, _, rest = chunk.partition("<<F>>")
        try:
            di = int(head.rstrip(">> \n"))
        except ValueError:
            continue
        cells = rest.split("<<F>>")
        for key, o in off.items():
            v = cells[o].strip() if o < len(cells) else ""
            if v and v != "missing value":
                ctx[key].append((di, v))
    return ctx


def fetch_context(dates: list[_dt.date]) -> dict[str, list[tuple[int, str]]]:
    script = build_context_script(dates)
    proc = subprocess.run([str(IX_OSA)], input=script, capture_output=True,
                          text=True, timeout=60)
    if proc.returncode != 0:
        return {k: [] for k in DAILY_COLS}
    return parse_context(proc.stdout, dates)


def expand_selections(answers: dict, ctx: dict) -> dict:
    """'Selecting rather than creating': a text answer that is ONLY day
    digits/commas (e.g. '3' or '2,5') expands to those days' context texts
    joined with '; '. Days without a context answer are ignored; a selection
    that matches nothing is kept verbatim."""
    out = dict(answers)
    for key, _label, _col, kind, ckey in FIELDS:
        if kind == "num" or not ckey:
            continue
        val = (out.get(key) or "").strip()
        if not val or not all(c in "1234567, " for c in val):
            continue
        by_day = dict(ctx.get(ckey, []))
        picks = [by_day[int(c) - 1] for c in val.replace(",", " ").split()
                 if c.isdigit() and (int(c) - 1) in by_day]
        if picks:
            out[key] = "; ".join(picks)
    return out


# ---------------------------------------------------------------------------
# Week-completeness gate (user decision 2026-07-21: missing daily surveys /
# time recording / 0l must be backfilled BEFORE 1s can run)
# ---------------------------------------------------------------------------

# A day with less than this many tracked Toggl minutes counts as a recording
# gap. Tracking here is near-total (sleep included), so a real day is ~24h;
# 20h leaves slack for edge entries without letting a half-recorded day pass.
MIN_TRACKED_MIN = 20 * 60


def build_0l_script(dates: list[_dt.date]) -> str:
    """Read the 0n sheet's 0l column for each date (0n col C = real date
    cells, so match displayed month/day; 0l column resolved via neon-cols)."""
    sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
    try:
        from neon import cols as _cols
        col = _cols.maybe_col("0n", "0l") or "S"
    except Exception:
        col = "S"
    conds = "\n".join(
        '    if md = "%d/%d" then set end of hits to {%d, r}' % (d.month, d.day, i)
        for i, d in enumerate(dates))
    return f'''tell application "Microsoft Excel"
  set ws to sheet "0n" of workbook "{WORKBOOK}"
  set tmpC to value of range "C3:C500" of ws
  set hits to {{}}
  repeat with r from 3 to 500
    set cv to item 1 of (item (r - 2) of tmpC)
    if cv is not missing value then
      try
        set md to (((month of cv) as integer) as text) & "/" & ((day of cv) as text)
{conds}
      end try
    end if
  end repeat
  set out to ""
  repeat with h in hits
    set vv to ""
    try
      set vv to (value of range ("{col}" & (item 2 of h)) of ws) as text
    end try
    set out to out & "<<D>>" & (item 1 of h) & "<<F>>" & vv
  end repeat
  return out
end tell'''


def _toggl_minutes_by_day(dates: list[_dt.date]) -> dict[_dt.date, int]:
    """Tracked minutes per local day for the week, via the Toggl v9 API.
    Empty dict on any failure (the gate then skips the tracking check
    rather than false-blocking on a network error)."""
    import base64
    import urllib.request
    try:
        cj = json.loads((Path.home() / ".claude.json").read_text())
        key = cj["mcpServers"]["toggl_server"]["env"]["TOGGL_API_KEY"]
        start = dates[0].isoformat()
        end = (dates[-1] + _dt.timedelta(days=1)).isoformat()
        url = ("https://api.track.toggl.com/api/v9/me/time_entries"
               f"?start_date={start}&end_date={end}")
        req = urllib.request.Request(url)
        req.add_header("Authorization", "Basic " + base64.b64encode(
            f"{key}:api_token".encode()).decode())
        with urllib.request.urlopen(req, timeout=20) as r:
            entries = json.loads(r.read())
    except Exception:
        return {}
    mins: dict[_dt.date, int] = {d: 0 for d in dates}
    for e in entries:
        dur = e.get("duration", 0)
        if dur <= 0:
            continue
        try:
            d = _dt.datetime.fromisoformat(
                e["start"].replace("Z", "+00:00")).astimezone().date()
        except (KeyError, ValueError):
            continue
        if d in mins:
            mins[d] += dur // 60
    return mins


def check_week(dates: list[_dt.date], ctx: dict | None = None) -> dict:
    """Blockers for running 1s: {'missing_0s': [date...], 'missing_0l':
    [date...], 'low_tracking': [(date, minutes)...]}. Empty lists = clear."""
    ctx = ctx if ctx is not None else fetch_context(dates)
    filled = {di for di, _t in ctx.get("titles", [])}
    missing_0s = [dates[i] for i in range(7) if i not in filled]

    missing_0l = list(dates)
    proc = subprocess.run([str(IX_OSA)], input=build_0l_script(dates),
                          capture_output=True, text=True, timeout=60)
    if proc.returncode == 0:
        done = set()
        for chunk in proc.stdout.split("<<D>>")[1:]:
            di, _, vv = chunk.partition("<<F>>")
            try:
                if float(vv.strip() or 0) > 0:
                    done.add(int(di))
            except ValueError:
                pass
        missing_0l = [dates[i] for i in range(7) if i not in done]

    mins = _toggl_minutes_by_day(dates)
    low_tracking = [(d, m) for d, m in sorted(mins.items())
                    if m < MIN_TRACKED_MIN]
    return {"missing_0s": missing_0s, "missing_0l": missing_0l,
            "low_tracking": low_tracking}


def format_blockers(blockers: dict) -> str:
    lines = []
    for d in blockers.get("missing_0s", []):
        lines.append("  0s survey missing for %s → /0s %s" % (_mdy(d), d.isoformat()))
    for d in blockers.get("missing_0l", []):
        lines.append("  0l not marked for %s → /did 0l %d/%d" % (_mdy(d), d.month, d.day))
    for d, m in blockers.get("low_tracking", []):
        lines.append("  only %dh%02dm tracked on %s → backfill via /tg" % (m // 60, m % 60, _mdy(d)))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Write to 1分+1s
# ---------------------------------------------------------------------------

def build_applescript(answers: dict, sunday: _dt.date) -> str:
    """Write filled answers to the review week's row (col A = M.W label).
    Only non-empty fields are written, so blanks never clobber cells."""
    label = week_row_label(sunday)
    L = [
        'tell application "Microsoft Excel"',
        '  set wb to workbook "%s"' % WORKBOOK,
        '  set ws to worksheet "%s" of wb' % SHEET,
        '  set tmpA to value of range "A2:A80" of ws',
        '  set weekRow to 0',
        '  repeat with r from 2 to 80',
        '    set av to ""',
        '    try',
        '      set av to (item 1 of (item (r - 1) of tmpA))',
        '    end try',
        '    if av is not missing value and av is not "" then',
        '      try',
        '        if (av as text) = "%s" then' % label,
        '          set weekRow to r',
        '          exit repeat',
        '        end if',
        '      end try',
        '    end if',
        '  end repeat',
        '  if weekRow = 0 then return "ERROR: week %s not found in %s col A"' % (label, SHEET),
        '  set wrote to 0',
    ]
    for key, _label, col, kind, _ckey in FIELDS:
        val = (answers.get(key) or "").strip()
        if not val:
            continue
        if kind == "num":
            if not _is_num(val):
                continue
            L.append('  set value of range ("%s" & weekRow) of ws to %s' % (col, float(val)))
        else:
            L.append('  set value of range ("%s" & weekRow) of ws to %s' % (col, _as_applescript(val)))
        L.append('  set wrote to wrote + 1')
    L += [
        '  save wb',
        '  return "OK: weekRow=" & weekRow & " wrote=" & wrote',
        'end tell',
    ]
    return "\n".join(L)


def write_answers(answers: dict, sunday: _dt.date) -> str:
    script = build_applescript(answers, sunday)
    proc = subprocess.run([str(IX_OSA)], input=script, capture_output=True,
                          text=True, timeout=60)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or out.startswith("ERROR"):
        raise RuntimeError(out or proc.stderr.strip() or "ix-osa failed")
    return out


# ---------------------------------------------------------------------------
# Full-screen form (prompt_toolkit) — same interaction grammar as 0s.py
# ---------------------------------------------------------------------------

def _ctx_lines(ctx_list: list[tuple[int, str]], dates: list[_dt.date]) -> str:
    lines = []
    for di, text in ctx_list:
        d = dates[di]
        one = " / ".join(t.strip() for t in text.splitlines() if t.strip())
        lines.append("%d %s %d/%d · %s" % (di + 1, DAY_ABBR[di], d.month, d.day, one))
    return "\n".join(lines)


def run_form(sunday: _dt.date, dates: list[_dt.date], ctx: dict) -> dict | None:
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, ScrollablePane, VSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Frame, TextArea

    areas = {}
    rows = []
    for key, label, _col, kind, ckey in FIELDS:
        ml = kind == "textml"
        area = TextArea(multiline=ml, height=(3 if ml else 1), wrap_lines=True,
                        style="class:input", scrollbar=ml)
        areas[key] = (area, kind, label)
        tag = " #" if kind == "num" else (" (digit=pick day)" if ckey else "")
        lbl = Window(FormattedTextControl(label + tag), width=28,
                     style="class:label", dont_extend_width=True)
        if ckey and ctx.get(ckey):
            hint = _ctx_lines(ctx[ckey], dates)
            rows.append(Window(FormattedTextControl(hint), style="class:ctx",
                               height=len(hint.splitlines()), wrap_lines=False))
        rows.append(VSplit([lbl, Window(width=1, char=" "), area]))

    msg = {"text": ""}
    status = Window(FormattedTextControl(
        lambda: msg["text"] or "digits pick day answers · Enter/Tab next · ^S save · ^Q cancel"),
        height=1, style="class:status")

    body = HSplit(rows, padding=0)
    root = HSplit([Frame(ScrollablePane(body),
                         title="1s · week %s (%s–%s)" % (
                             week_row_label(sunday), _mdy(sunday), _mdy(dates[-1]))),
                   status])

    ordered = [(k, areas[k][0], areas[k][1]) for k, *_ in FIELDS]
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
        ("ctx", "fg:#5f875f"),
        ("status", "bg:#111111 fg:#00e676"),
        ("frame.border", "fg:#333333"),
    ])

    app = Application(layout=Layout(root, focused_element=areas[FIELDS[0][0]][0]),
                      key_bindings=kb, full_screen=True, style=style,
                      mouse_support=True)
    if app.run() != "submit":
        return None
    return {k: a.text.strip() for k, (a, _kind, _lbl) in areas.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", help="any date in the review week (default: last completed week)")
    ap.add_argument("--from-json", help="write answers from a JSON file instead of the form")
    ap.add_argument("--print-script", action="store_true", help="print AppleScript, do not write")
    ap.add_argument("--print-context", action="store_true", help="dump the fetched daily context")
    ap.add_argument("--check-week", action="store_true",
                    help="report week-completeness blockers and exit (3 = blockers found)")
    ap.add_argument("--force", action="store_true",
                    help="open the form even with week-completeness blockers")
    ap.add_argument("--no-mark", action="store_true",
                    help="do not mark the 1s task done after a successful save")
    args = ap.parse_args()

    sunday, saturday = week_range(args.date)
    dates = [sunday + _dt.timedelta(days=i) for i in range(7)]

    if args.print_context:
        print(json.dumps(fetch_context(dates), ensure_ascii=False, indent=2))
        return 0

    if args.check_week:
        blockers = check_week(dates)
        if any(blockers.values()):
            print("1s blocked — backfill the week first:")
            print(format_blockers(blockers))
            return 3
        print("week %s complete — 1s clear to run" % week_row_label(sunday))
        return 0

    if args.from_json:
        answers = json.loads(Path(args.from_json).read_text())
        ctx = fetch_context(dates) if not args.print_script else {k: [] for k in DAILY_COLS}
    else:
        ctx = fetch_context(dates)
        # Hard gate (user decision 2026-07-21): the weekly review may not run
        # on an incomplete week — missing daily surveys / time recording / 0l
        # must be backfilled first. --force is the deliberate escape hatch.
        if not args.force:
            blockers = check_week(dates, ctx)
            if any(blockers.values()):
                print("1s blocked — backfill the week first:")
                print(format_blockers(blockers))
                print("(rerun with --force to fill the survey anyway)")
                return 3
        answers = run_form(sunday, dates, ctx)
        if answers is None:
            print("1s survey cancelled.")
            return 1
    answers = expand_selections(answers, ctx)

    if args.print_script:
        print(build_applescript(answers, sunday))
        return 0

    filled = sum(1 for k, _l, _c, _kind, _ck in FIELDS if (answers.get(k) or "").strip())
    result = write_answers(answers, sunday)
    msg = "1s → %s week %s (%d fields) · %s" % (SHEET, week_row_label(sunday), filled, result)

    # Completing the survey IS completing the weekly 1s task (user decision
    # 2026-07-21: "I need to complete the survey before task is complete") —
    # mark it here, not in the /1s skill flow.
    if not args.no_mark:
        try:
            subprocess.run(["/usr/bin/python3",
                            str(Path.home() / "i446-monorepo/tools/did/run.py"), "1s"],
                           capture_output=True, text=True, timeout=120)
            msg += " · 1s marked done"
        except Exception as e:  # noqa: BLE001
            msg += " · 1s mark FAILED: %s" % e

    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
