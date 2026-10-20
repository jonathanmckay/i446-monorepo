#!/usr/bin/env python3
"""split-form.py — dtd ctrl-p (split) as ONE terminal form (2026-10-04).

All four questions on screen at once; Tab / ↓ next field, Shift-Tab / ↑
previous, Enter submits, Esc cancels. "Points remaining" prefills with
total − done and follows "points done" until you type in it yourself.

  split-form.py --title T --total N|? --open [|{ --close ]|} --out FILE

Runs on the controlling terminal (the caller redirects stdin/stdout to
/dev/tty). Writes FILE as four lines: done, did, remains, remaining. Exit 0
on submit, 1 on cancel (FILE untouched).
"""
import argparse
import curses
import locale
import sys

LABELS = ("Points done today", "What did you do", "What remains", "Points remaining")
NUMERIC = (True, False, False, True)


def run(stdscr, a):
    curses.curs_set(1)
    stdscr.keypad(True)
    total = int(a.total) if a.total.isdigit() else None
    vals = ["", "", "", ""]
    remaining_touched = False
    cur = 0
    err = ""

    def auto_remaining():
        if total is None or remaining_touched:
            return
        d = vals[0]
        vals[3] = str(max(0, total - int(d))) if d.isdigit() else str(total)

    auto_remaining()
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        tot = f"{a.open}{a.total}{a.close}"
        stdscr.addnstr(0, 0, f"Split: {a.title}", w - 1, curses.A_BOLD)
        stdscr.addnstr(1, 0, f"total {tot} · Tab/↑↓ move · Enter split · Esc cancel", w - 1, curses.A_DIM)
        lw = max(len(s) for s in LABELS) + 2
        for i, lab in enumerate(LABELS):
            y = 3 + i * 2
            attr = curses.A_REVERSE if i == cur else curses.A_NORMAL
            stdscr.addnstr(y, 0, f"{lab}:".ljust(lw), w - 1, curses.A_BOLD if i == cur else curses.A_NORMAL)
            hint = ""
            if i in (1, 2) and not vals[i]:
                hint = "(blank = keep the task name)"
            if i == 3 and total is None and not vals[3]:
                hint = "(blank = 0)"
            field = vals[i] if vals[i] else hint
            stdscr.addnstr(y, lw, (field + " ").ljust(max(1, w - lw - 1))[: max(1, w - lw - 1)], max(1, w - lw - 1),
                           attr if vals[i] or i == cur else curses.A_DIM)
        if err:
            stdscr.addnstr(3 + len(LABELS) * 2, 0, err, w - 1, curses.A_BOLD)
        y = 3 + cur * 2
        stdscr.move(y, min(w - 1, lw + curses_width(vals[cur])))
        stdscr.refresh()

        try:
            ch = stdscr.get_wch()
        except curses.error:
            continue
        err = ""
        if ch in ("\x1b",):
            return None
        if ch in ("\n", "\r", curses.KEY_ENTER):
            if not vals[0].isdigit():
                err = "Points done today must be a number (Esc to cancel)."
                cur = 0
                continue
            if vals[3] and not vals[3].isdigit():
                err = "Points remaining must be a number."
                cur = 3
                continue
            return vals
        if ch in ("\t", curses.KEY_DOWN):
            cur = (cur + 1) % len(LABELS)
        elif ch in (curses.KEY_BTAB, curses.KEY_UP):
            cur = (cur - 1) % len(LABELS)
        elif ch in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            vals[cur] = vals[cur][:-1]
            if cur == 3:
                remaining_touched = True
        elif ch == "\x15":  # ctrl-u clears the field
            vals[cur] = ""
            if cur == 3:
                remaining_touched = True
        elif isinstance(ch, str) and ch.isprintable():
            if NUMERIC[cur] and not ch.isdigit():
                err = f"{LABELS[cur]} takes digits only."
                continue
            vals[cur] += ch
            if cur == 3:
                remaining_touched = True
        if cur != 3 or not remaining_touched:
            auto_remaining()


def curses_width(s: str) -> int:
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--title", default="")
    p.add_argument("--total", default="?")
    p.add_argument("--open", default="[")
    p.add_argument("--close", default="]")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    locale.setlocale(locale.LC_ALL, "")
    import os
    os.environ.setdefault("ESCDELAY", "25")
    res = curses.wrapper(run, a)
    if res is None:
        return 1
    with open(a.out, "w") as f:
        f.write("\n".join(v.replace("\n", " ") for v in res) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
