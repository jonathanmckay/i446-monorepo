#!/usr/bin/env python3
"""1hcme — weekly emotional check-in (the `1 hcme` 1neon card).

Pulls the week's daily 0s survey rows from the Neon `0s897` tab, renders them
in the terminal with the SAME cell colors Excel shows (conditional-format
fill + font, read via `display format` on Ix), then asks:

    What did I learn this week about my emotions that I should remember?

The answer is written to the `1 hcme` column (X) of the week's THURSDAY row
in `0s897` (that is where every weekly check-in has lived since 1/1/26 —
col A's M.W label on that row names the week), then the `1 hcme` weekly
habit is marked done through the normal /did path (1n+ credit + Todoist
close).

Which week: the `1 hcme` card recurs on Thursday (1n+ row 3 = 5). With no
argument the target is the most recent Thursday on/before today; if that
row's X is already filled and a later Thursday exists on the sheet, the next
Thursday (the open card) is targeted instead. `YYYY-MM-DD` targets the
Thursday of the Sun-Sat week containing that date; `M.W` targets that fiscal
week's Thursday (same label system as /1s897).

Display window: the Friday after the previous check-in through the target
week's Saturday (clipped to today) — i.e. "everything since the last 1 hcme".
The previous check-in's own text is shown first for continuity.

Usage:
  python3 1hcme.py                       # interactive
  python3 1hcme.py 2026-10-15            # a specific week
  python3 1hcme.py 10.3                  # by fiscal-week label
  python3 1hcme.py --print-context       # render only, no question, no write
  python3 1hcme.py --from-text "…"       # non-interactive write
  python3 1hcme.py --print-script --from-text "…"   # AppleScript only
  python3 1hcme.py --no-mark             # write X but leave the card open
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
from review_form_writer import run_bounded  # noqa: E402

IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.sh"
DID_RUN = Path.home() / "i446-monorepo/tools/did/run.py"
DID_FAST = Path.home() / "i446-monorepo/tools/did/did-fast.py"
WORKBOOK = "Neon分v12.2.xlsx"
SHEET = "0s897"
HCME_COL = "X"
HABIT = "1 hcme"
QUESTION = "What did I learn this week about my emotions that I should remember?"

# Columns pulled from each 0s897 row, in the order they are read (A..X).
# Rendering below picks from these by letter.
READ_COLS = [chr(c) for c in range(ord("A"), ord("X") + 1)]
# Table layout. Numeric columns are fixed width; text columns share the rest
# at CELL_W each, lowest-priority columns dropped first when the terminal is
# narrow (--cell / --cols override).
NUM_COLS = [  # (col, header, width)
    ("P", "⌈", 4), ("Q", "⌊", 4), ("R", "x̄", 4), ("V", "他⌈", 5), ("W", "他⌊", 5),
    ("K", "霓", 3), ("L", "帮", 3), ("M", "身", 3), ("O", "₹", 6),
]
TEXT_COLS = [  # (col, header) in priority order
    ("E", "Title"), ("H", "Win"), ("I", "Learning"), ("G", "Thankful"),
    ("S", "Proud·o"), ("T", "Learn·o"), ("D", "Motivation"), ("F", "Noticed"),
    ("N", "Body"),
]
DAY_ABBR = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]  # weekday() order


# ---------------------------------------------------------------------------
# Week resolution
# ---------------------------------------------------------------------------

def thursday_of_week(d: _dt.date) -> _dt.date:
    """Thursday of the Sun-anchored week containing d."""
    sunday = d - _dt.timedelta(days=(d.weekday() + 1) % 7)
    return sunday + _dt.timedelta(days=4)


def resolve_target(arg: str | None, today: _dt.date) -> _dt.date:
    if not arg:
        t = thursday_of_week(today)
        return t if t <= today else t - _dt.timedelta(days=7)
    if re.fullmatch(r"\d{1,2}\.\d{1,2}", arg):
        return thursday_for_label(arg)
    return thursday_of_week(_dt.date.fromisoformat(arg))


def build_label_script(label: str) -> str:
    """Thursday row of the week whose col-A label is `label`. 0s897's own
    labels (week containing Jan 1 = 1.1) run one week ahead of the fiscal
    labels in /1s and /1s897 (week 1 = first Sunday on/after Jan 1), so the
    label is resolved against this sheet, never computed."""
    return f'''tell application "Microsoft Excel"
  set ws to worksheet "{SHEET}" of workbook "{WORKBOOK}"
  set tmpA to string value of range "A2:A600" of ws
  set tmpB to string value of range "B2:B600" of ws
  set tmpC to string value of range "C2:C600" of ws
  repeat with r from 2 to 600
    try
      if ((item 1 of (item (r - 1) of tmpA)) as text) = "{label}" and ((item 1 of (item (r - 1) of tmpC)) as text) = "Th" then
        return "OK|" & ((item 1 of (item (r - 1) of tmpB)) as text)
      end if
    end try
  end repeat
  return "ERROR: no Thursday row labeled {label} in {SHEET}"
end tell'''


def thursday_for_label(label: str) -> _dt.date:
    proc = run_bounded([str(IX_OSA)], input=build_label_script(label), timeout=60)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out.startswith("OK|"):
        raise SystemExit(out or proc.stderr.strip() or "ix-osa failed")
    m, d, y = out[3:].split("/")
    return _dt.date(2000 + int(y), int(m), int(d))


def window(target: _dt.date, today: _dt.date) -> list[_dt.date]:
    start = target - _dt.timedelta(days=6)           # Friday after prior card
    end = min(today, target + _dt.timedelta(days=2))  # target week's Saturday
    if end < start:
        end = start
    return [start + _dt.timedelta(days=i) for i in range((end - start).days + 1)]


def _mdy(d: _dt.date) -> str:
    return "%d/%d/%s" % (d.month, d.day, d.strftime("%y"))


# ---------------------------------------------------------------------------
# Fetch: values + display-format colors, one Ix round trip
# ---------------------------------------------------------------------------

COLOR_COLS = sorted({c for c, _h, _w in NUM_COLS} | {c for c, _h in TEXT_COLS})
FETCH_TIMEOUT = 240  # Excel on Ix can take >1 min per AppleEvent when busy (2026-10-17)


def build_fetch_script(dates: list[_dt.date]) -> str:
    """One row-range read for values, then display-format fill/font only for
    the rendered columns (2 AppleEvents per cell instead of 3 for every A..X
    cell — the first cut timed out at 90s against a busy Excel)."""
    conds = "\n".join(
        '    if bv = "%s" then set end of hits to {%d, r}' % (_mdy(d), i)
        for i, d in enumerate(dates))
    ccols = ", ".join('"%s"' % c for c in COLOR_COLS)
    return f'''tell application "Microsoft Excel"
  set ws to worksheet "{SHEET}" of workbook "{WORKBOOK}"
  set tmpB to string value of range "B2:B600" of ws
  set hits to {{}}
  repeat with r from 2 to 600
    set bv to ""
    try
      set bv to (item 1 of (item (r - 1) of tmpB)) as text
    end try
{conds}
  end repeat
  set out to ""
  repeat with h in hits
    set di to item 1 of h
    set rr to item 2 of h
    set out to out & "<<ROW>>" & di & "|" & rr
    set tmpR to string value of range ("A" & rr & ":X" & rr) of ws
    set rowVals to item 1 of tmpR
    repeat with v in rowVals
      set vv to ""
      try
        set vv to v as text
      end try
      set out to out & "<<V>>" & vv
    end repeat
    repeat with c in {{{ccols}}}
      set cel to range ((c as text) & rr) of ws
      set bg to {{255, 255, 255}}
      try
        set bg to color of interior object of display format of cel
      end try
      set fg to {{0, 0, 0}}
      try
        set fg to color of font object of display format of cel
      end try
      set out to out & "<<C>>" & (c as text) & "|" & (item 1 of bg) & "," & (item 2 of bg) & "," & (item 3 of bg) & "|" & (item 1 of fg) & "," & (item 2 of fg) & "," & (item 3 of fg)
    end repeat
  end repeat
  return out
end tell'''


def parse_fetch(raw: str) -> dict[int, dict]:
    """→ {day_index: {"row": int, "cells": {col: (value, bg, fg)}}}
    Every A..X column gets a value; colors default to white/black for
    columns not in COLOR_COLS."""
    out: dict[int, dict] = {}
    for chunk in raw.split("<<ROW>>")[1:]:
        head, _, rest = chunk.partition("<<V>>")
        try:
            di_s, row_s = head.strip().split("|")
            di, row = int(di_s), int(row_s)
        except ValueError:
            continue
        vals_part, _, colors_part = rest.partition("<<C>>")
        vals = vals_part.split("<<V>>")
        cells = {}
        for col, v in zip(READ_COLS, vals):
            v = v.strip()
            cells[col] = ("" if v == "missing value" else v, (255, 255, 255), (0, 0, 0))
        for c in colors_part.split("<<C>>"):
            parts = c.split("|")
            if len(parts) != 3:
                continue
            col, bg_s, fg_s = parts
            try:
                bg = tuple(int(x) for x in bg_s.split(","))
                fg = tuple(int(x) for x in fg_s.split(","))
            except ValueError:
                continue
            if col in cells:
                cells[col] = (cells[col][0], bg, fg)
        out[di] = {"row": row, "cells": cells}
    return out


def fetch(dates: list[_dt.date]) -> dict[int, dict]:
    proc = run_bounded([str(IX_OSA)], input=build_fetch_script(dates), timeout=FETCH_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "ix-osa failed").strip())
    return parse_fetch(proc.stdout)


# ---------------------------------------------------------------------------
# Render (truecolor ANSI, Excel's display-format colors)
# ---------------------------------------------------------------------------

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"


def _is_white(rgb) -> bool:
    return all(v >= 250 for v in rgb)


def paint(text: str, bg, fg, *, pad: int = 0) -> str:
    """Excel-faithful chip: fill + font color. A white fill is rendered as the
    terminal default (no glare on dark themes); black-on-white becomes the
    default fg so it stays readable on either theme."""
    if pad:
        text = text.ljust(pad)
    if _is_white(bg):
        if fg == (0, 0, 0):
            return text
        return "\x1b[38;2;%d;%d;%dm%s%s" % (*fg, text, RESET)
    return "\x1b[48;2;%d;%d;%dm\x1b[38;2;%d;%d;%dm%s%s" % (*bg, *fg, text, RESET)


def _oneline(s: str) -> str:
    return " / ".join(p.strip() for p in s.splitlines() if p.strip())


def _dw(s: str) -> int:
    """Display width: CJK (W/F) characters take two terminal columns."""
    import unicodedata
    return sum(0 if unicodedata.combining(ch) else
               2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _clip(s: str, width: int) -> str:
    """Cut to `width` display columns, ellipsis on the cut."""
    if _dw(s) <= width:
        return s
    out, w = "", 0
    for ch in s:
        cw = _dw(ch)
        if w + cw > width - 1:
            break
        out += ch
        w += cw
    return out + "…"


def _fit(s: str, width: int) -> str:
    s = _clip(s, width)
    return s + " " * (width - _dw(s))


DAY_W = 8
CELL_W = 24


def _cell(cells: dict, col: str, width: int, *, num: bool = False) -> str:
    val, bg, fg = cells.get(col, ("", (255, 255, 255), (0, 0, 0)))
    val = _oneline(val)
    if num:
        val = val.strip()
    if not val or val.lower() == "n/a":
        return DIM + ("·".rjust(width - 1) + " " if num else _fit("·", width)) + RESET
    if num:
        return paint((_clip(val, width - 1)).rjust(width - 1) + " ", bg, fg)
    return paint(" " + _fit(val, width - 1), bg, fg)


MIN_TEXT_COLS = 3   # shrink cells (down to MIN_CELL_W) before dropping below this
MIN_CELL_W = 12


def layout(width: int, cell_w: int, max_text: int | None) -> tuple[list[tuple[str, str]], int]:
    """(text columns that fit, cell width). Priority order decides which
    columns survive; table order stays as listed in TEXT_COLS. On a narrow
    pane the cell width shrinks (to MIN_CELL_W) so at least MIN_TEXT_COLS
    columns show before any are dropped."""
    fixed = DAY_W + sum(w + 1 for _c, _h, w in NUM_COLS)
    avail = max(0, width - fixed)
    n = avail // (cell_w + 1)
    if n < MIN_TEXT_COLS:
        cell_w = max(MIN_CELL_W, avail // MIN_TEXT_COLS - 1)
        n = max(1, avail // (cell_w + 1))
    if max_text is not None:
        n = min(n, max_text)
    keep = {c for c, _h in TEXT_COLS[:n]}
    return [(c, h) for c, h in TEXT_COLS if c in keep], cell_w


def render(dates: list[_dt.date], recs: dict[int, dict], target: _dt.date,
           prev: dict | None, *, cell_w: int = CELL_W, max_text: int | None = None) -> str:
    width = shutil.get_terminal_size((160, 40)).columns
    text_cols, cell_w = layout(width, cell_w, max_text)
    out = []
    tlabel = ""
    if (ti := dates.index(target) if target in dates else None) is not None:
        tlabel = recs.get(ti, {}).get("cells", {}).get("A", ("",))[0]
    out.append(BOLD + "1 hcme · week %s · check-in row Th %d/%d" % (
        tlabel or "?", target.month, target.day) + RESET)
    if prev:
        pv = prev["cells"].get(HCME_COL, ("",))[0].strip()
        if pv:
            out.append(DIM + "last check-in (Th %s):" % _mdy(target - _dt.timedelta(days=7)) + RESET)
            for ln in pv.splitlines():
                if ln.strip():
                    out.append("  " + _clip(ln.strip(), width - 4))
    out.append("")
    hdr = [BOLD + _fit("day", DAY_W) + RESET]
    hdr += [BOLD + " " * max(0, w - 1 - _dw(h)) + h + " " + RESET for _c, h, w in NUM_COLS]
    hdr += [BOLD + _fit(" " + h, cell_w) + RESET for _c, h in text_cols]
    out.append(" ".join(hdr))
    for i, d in enumerate(dates):
        rec = recs.get(i)
        day = "%s %d/%d" % (DAY_ABBR[d.weekday()], d.month, d.day)
        day = (BOLD if d == target else "") + _fit(day, DAY_W) + RESET
        if not rec:
            out.append(day + " " + DIM + "(no 0s897 row)" + RESET)
            continue
        cells = rec["cells"]
        row = [day]
        row += [_cell(cells, c, w, num=True) for c, _h, w in NUM_COLS]
        row += [_cell(cells, c, cell_w) for c, _h in text_cols]
        out.append(" ".join(row))
    dropped = [h for c, h in TEXT_COLS if c not in dict(text_cols)]
    if dropped:
        out.append(DIM + "not shown (narrow terminal): " + ", ".join(dropped) + RESET)
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Write X + mark done
# ---------------------------------------------------------------------------

def _as_applescript(s: str) -> str:
    parts = s.split("\n")
    esc = [p.replace("\\", "\\\\").replace('"', '\\"') for p in parts]
    return " & linefeed & ".join('"%s"' % e for e in esc) if esc else '""'


def bulletize(text: str) -> str:
    """Match the sheet's existing style: one '• ' bullet per line."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(ln if ln.startswith(("•", "-", "*")) else "• " + ln
                     for ln in lines)


def build_write_script(row: int, text: str) -> str:
    return "\n".join([
        'tell application "Microsoft Excel"',
        '  set wb to workbook "%s"' % WORKBOOK,
        '  set ws to worksheet "%s" of wb' % SHEET,
        '  set value of range "%s%d" of ws to %s' % (HCME_COL, row, _as_applescript(text)),
        '  save wb',
        '  return "OK: %s!%s%d"' % (SHEET, HCME_COL, row),
        'end tell',
    ])


def write_answer(row: int, text: str) -> str:
    proc = run_bounded([str(IX_OSA)], input=build_write_script(row, text), timeout=90)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or out.startswith("ERROR"):
        raise RuntimeError(out or proc.stderr.strip() or "ix-osa failed")
    return out


def mark_done() -> str:
    env = dict(os.environ, DIDFAST_WATCHDOG_SECS="110")
    proc = run_bounded(["/usr/bin/python3", str(DID_RUN), HABIT], timeout=120, env=env)
    msg = (proc.stdout or proc.stderr or "").strip().splitlines()
    try:  # foreground refresh so an open dtd reloads at once (README rule)
        run_bounded(["/usr/bin/python3", str(DID_FAST), "--refresh-cache"], timeout=60)
    except Exception:  # noqa: BLE001
        pass
    return msg[-1] if msg else "(no output from did run.py)"


def _hold_pane(args) -> None:
    """A cmux tab we opened closes the instant this process exits, taking the
    error with it; under dtd's wrapper the wrapper already waits for a key."""
    if args.from_text or args.print_context or os.environ.get("DTD_HDR_FILE"):
        return
    try:
        input(DIM + "press Enter to close" + RESET)
    except EOFError:
        pass


def read_answer() -> str:
    print(BOLD + QUESTION + RESET)
    print(DIM + "(one thought per line; blank line or ^D to finish; empty = abort)" + RESET)
    lines: list[str] = []
    while True:
        try:
            ln = input("> ")
        except EOFError:
            print()
            break
        if not ln.strip():
            break
        lines.append(ln)
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="weekly 1 hcme emotional check-in")
    ap.add_argument("week", nargs="?", help="YYYY-MM-DD or M.W (default: open card)")
    ap.add_argument("--print-context", action="store_true")
    ap.add_argument("--from-text", help="non-interactive answer")
    ap.add_argument("--print-script", action="store_true")
    ap.add_argument("--no-mark", action="store_true", help="write X, leave the card open")
    ap.add_argument("--today", help="override today (tests)")
    ap.add_argument("--cell", type=int, default=CELL_W, help="text cell width (chars, default %d)" % CELL_W)
    ap.add_argument("--cols", type=int, help="max text columns (default: as many as fit)")
    args = ap.parse_args(argv)

    today = _dt.date.fromisoformat(args.today) if args.today else _dt.date.today()
    target = resolve_target(args.week, today)

    if args.print_script:
        row = int(os.environ.get("HCME_ROW", "0")) or 0
        print(build_write_script(row, bulletize(args.from_text or "")))
        return 0

    dates = window(target, today)
    prev_date = target - _dt.timedelta(days=7)
    print(DIM + "loading %s %s → %s from Ix… (Excel can take a minute when busy)" % (
        SHEET, _mdy(dates[0]), _mdy(dates[-1])) + RESET, flush=True)
    try:
        recs = fetch([prev_date] + dates)
    except Exception as e:  # noqa: BLE001
        print("✗ could not read %s from Ix: %s" % (SHEET, str(e).strip()[:300]))
        _hold_pane(args)
        return 1
    prev = recs.pop(0, None)
    recs = {i - 1: r for i, r in recs.items()}

    ti = dates.index(target) if target in dates else None
    trec = recs.get(ti) if ti is not None else None
    # No arg + this week's card already filled → roll forward to the next
    # Thursday if the sheet has that row.
    if not args.week and trec and trec["cells"].get(HCME_COL, ("",))[0].strip():
        nxt = target + _dt.timedelta(days=7)
        if nxt <= today + _dt.timedelta(days=6):
            ndates = window(nxt, today)
            nrecs = fetch([target] + ndates)
            if 1 in nrecs:  # next Thursday's week has rows
                prev = nrecs.pop(0, None)
                recs = {i - 1: r for i, r in nrecs.items()}
                dates, target = ndates, nxt
                ti = dates.index(target) if target in dates else None
                trec = recs.get(ti) if ti is not None else None

    print(render(dates, recs, target, prev, cell_w=args.cell, max_text=args.cols))

    if trec is None:
        print("✗ no %s row for Th %s — cannot write %s" % (SHEET, _mdy(target), HCME_COL))
        _hold_pane(args)
        return 1
    existing = trec["cells"].get(HCME_COL, ("",))[0].strip()
    if existing:
        print(BOLD + "already filled for this week (%s!%s%d):" % (SHEET, HCME_COL, trec["row"]) + RESET)
        for ln in existing.splitlines():
            print("  " + ln)
        print(DIM + "a new answer REPLACES it" + RESET)
        print()

    if args.print_context:
        return 0

    answer = args.from_text.strip() if args.from_text else read_answer()
    if not answer:
        print("✗ empty answer — nothing written; %s card left open" % HABIT)
        return 1
    text = bulletize(answer)

    print("Saving to Neon…", flush=True)
    try:
        res = write_answer(trec["row"], text)
    except Exception as e:  # noqa: BLE001
        rec_dir = Path.home() / ".cache/1hcme-recovery"
        rec_dir.mkdir(parents=True, exist_ok=True)
        p = rec_dir / ("%s.txt" % target.isoformat())
        p.write_text(text)
        print("✗ save FAILED: %s" % e)
        print("→ answer saved to %s — replay with --from-text once fixed" % p)
        _hold_pane(args)
        return 1
    print("✓ %s (week %s)" % (res, trec["cells"].get("A", ("",))[0] or "?"))

    if not args.no_mark:
        print("marking %s done…" % HABIT, flush=True)
        try:
            print("✓ " + mark_done())
        except Exception as e:  # noqa: BLE001
            print("✗ %s mark failed: %s — run `/did %s`" % (HABIT, e, HABIT))
            _hold_pane(args)
            return 1

    # Interactive runs close their own cmux tab after a successful save (same
    # convention as 1s-survey.py); failures above return early and leave the
    # pane open so the error stays readable. Inside dtd's inline-form wrapper
    # (DTD_FORM exports DTD_HDR_FILE) the pane IS dtd's — never close it.
    if not args.from_text and not os.environ.get("DTD_HDR_FILE") and shutil.which("cmux"):
        time.sleep(1.5)
        try:
            subprocess.run(["cmux", "close-surface"], capture_output=True, text=True, timeout=10)
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
