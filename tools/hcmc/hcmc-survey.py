#!/usr/bin/env python3
"""hcmc-survey — the weekly media check-in behind /1hcmc.

Question: What did I learn about media consumption this week? How can I do better next week?

First, 3 bullets on each book finished in the week and not yet reviewed. Those go
to the book's review file as a draft jmreads shows, not to Neon (2026-11-05).

The answer lives in Neon `1分+1s` column AO (the media "Notes" column, next to the
新闻/词汇/-1/-2/-3/书 counts) on the row of the current Sunday-anchored fiscal week,
the same row `/book finished` bumps for books (col AM), unless last week's note is
blank: then it fills last week first. The write goes through the excel-http
daemon (journaled); ix-osa.sh does the reads and is the daemon-down fallback.

Usage:
  hcmc-survey.py --show                 print last week's and this week's AO notes + the target row
  hcmc-survey.py --from-text "…"        write the answer (one "• " bullet per line) to AO<row>
  hcmc-survey.py --from-text "…" --mark ...and mark the `1 hcmc` habit done (did-fast) + refresh dtd
  hcmc-survey.py --books                print the week's unreviewed books: id, title, author
  hcmc-survey.py --book-note ID --from-text "…"   save one book's bullets to its review file
  hcmc-survey.py                        interactive: books, show, ask, write, mark
Options: --today YYYY-MM-DD (tests), --print-script (dump the AppleScript instead of running it)
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.expanduser("~/i446-monorepo/lib"))
from neon import excel as neon_excel  # noqa: E402  excel-http daemon client (journaled writes)
from survey_input import read_line  # noqa: E402
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("book_finish", os.path.join(os.path.dirname(os.path.abspath(__file__)), "book-finish.py"))
_bf = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_bf)   # reuse week_row_label

IX_OSA = os.path.expanduser("~/.claude/skills/_lib/ix-osa.sh")
JMREADS = os.path.expanduser("~/i446-monorepo/tools/personal-dashboard/jmreads.py")
WEEK_CALC = os.path.expanduser("~/.claude/skills/1s897/week_calc.py")
DID_FAST = os.path.expanduser("~/i446-monorepo/tools/did/did-fast.py")
WORKBOOK = "Neon分v12.2.xlsx"
SHEET = "1分+1s"
COL = "AO"
HABIT = "1 hcmc"
QUESTION = "What did I learn about media consumption this week? How can I do better next week?"
FIRST_ROW, LAST_ROW = 2, 120


def _jmreads():
    """jmreads.py, whose review-file reads and save_draft these notes share."""
    if "jmreads_mod" not in sys.modules:
        spec = _ilu.spec_from_file_location("jmreads_mod", JMREADS)
        mod = _ilu.module_from_spec(spec)
        sys.modules["jmreads_mod"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["jmreads_mod"]


def report_week() -> tuple[datetime.date, datetime.date]:
    """The Wed-Tue week /1hcmc's book report covers."""
    out = subprocess.run([sys.executable, WEEK_CALC], capture_output=True, text=True).stdout
    start, end = out.split()[:2]
    return datetime.date.fromisoformat(start), datetime.date.fromisoformat(end)


def unreviewed_books(start: datetime.date, end: datetime.date, jr=None) -> list[dict]:
    """Books finished in [start, end] with no review, score or draft notes."""
    jr = jr or _jmreads()
    out = []
    for b in jr.load_books(jr.REVIEWS_DIR, jr.BLOG_REVIEWS_DIR):
        if b["reviewed"] or not b["finished"] or not start <= b["finished"] <= end:
            continue
        path = jr.find_book_file(b["id"])
        if path and not re.search(r"^> draft ", path.read_text(encoding="utf-8"), re.M):
            out.append(b)
    return out


def save_book_notes(book_id: str, text: str, jr=None) -> tuple[bool, str]:
    return (jr or _jmreads()).save_draft(book_id, bulletize(text))


def ask_book_notes(start: datetime.date, end: datetime.date, jr=None) -> None:
    """Up to 3 bullets per book; a blank line ends that book."""
    for b in unreviewed_books(start, end, jr):
        print(f"\n{b['title']} — {b['author']}\nWhat did you think? (3 bullets; blank skips)")
        lines = []
        for i in range(1, 4):
            ln = read_line(f"{i}> ")
            if not ln:
                break
            lines.append(ln)
        if lines:
            print(save_book_notes(b["id"], "\n".join(lines), jr)[1])


def sunday_of(d: datetime.date) -> datetime.date:
    return d - datetime.timedelta(days=(d.weekday() + 1) % 7)


def _as_applescript(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def bulletize(text: str) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return "\n".join(l if l.startswith(("• ", "* ", "- ")) else "• " + l for l in lines)


def osa(script: str) -> str:
    r = subprocess.run([IX_OSA], input=script, capture_output=True, text=True, timeout=240)
    out = (r.stdout or "").strip()
    if r.returncode != 0 or out.startswith(("ERROR", "ERR:")):
        raise SystemExit(f"ix-osa failed ({r.returncode}): {out or r.stderr.strip()}")
    return out


def show_script(label: str, prev_label: str) -> str:
    return "\n".join([
        'tell application "Microsoft Excel"',
        f'  set ws to sheet "{SHEET}" of workbook "{WORKBOOK}"',
        '  set out to ""',
        f'  repeat with r from {FIRST_ROW} to {LAST_ROW}',
        '    set a to string value of cell 1 of row r of ws',
        f'    if a is "{prev_label}" then set out to out & "PREV\t" & r & "\t" & (string value of range ("{COL}" & r) of ws) & linefeed & "END" & linefeed',
        f'    if a is "{label}" then set out to out & "CUR\t" & r & "\t" & (string value of range ("{COL}" & r) of ws) & linefeed & "END" & linefeed',
        '  end repeat',
        '  return out',
        'end tell',
    ])


def write_script(row: int, text: str) -> str:
    return "\n".join([
        'tell application "Microsoft Excel"',
        f'  set ws to sheet "{SHEET}" of workbook "{WORKBOOK}"',
        f'  set value of range "{COL}{row}" of ws to {_as_applescript(text)}',
        '  save workbook "%s"' % WORKBOOK,
        f'  return "OK: {SHEET}!{COL}{row}"',
        'end tell',
    ])


def write_answer(label: str, row: int, text: str) -> str:
    """Daemon first (Neon-off-Excel step 2, 2026-11-06): row is the one --show
    displayed, label journals the week. A stalled Excel gets one retry; raw
    ix-osa only when the daemon is unreachable. On failure the answer is
    printed, because nothing else keeps it."""
    items = [{"col": COL, "value": text, "mode": "text"}]
    try:
        r = neon_excel.batch_write(SHEET, items, row=row, date=label, src="1hcmc", fallback=False)
        if not r.get("ok") and any(k in str(r.get("error", "")) for k in ("timeout", "excel_error")):
            r = neon_excel.batch_write(SHEET, items, row=row, date=label, src="1hcmc", fallback=False)
        if r.get("ok"):
            return f"OK: {SHEET}!{COL}{row}"
        if not r.get("fallback"):
            raise SystemExit(f"{SHEET}!{COL}{row} write failed: {r.get('error') or r}\nnot saved:\n{text}")
    except RuntimeError:
        raise
    except Exception as e:  # noqa: BLE001 — transport trouble → raw fallback
        print(f"1hcmc daemon path failed ({e}); using ix-osa", file=sys.stderr)
    return osa(write_script(row, text))


def parse_show(raw: str) -> dict:
    out, cur = {}, None
    for line in raw.splitlines():
        if cur is None and line.startswith(("PREV\t", "CUR\t")):
            kind, row, first = line.split("\t", 2)
            cur = [kind, int(row), [first]]
        elif cur and line == "END":
            out[cur[0]] = (cur[1], "\n".join(cur[2]).strip()); cur = None
        elif cur:
            cur[2].append(line)
    return out


def target(info: dict, label: str, prev_label: str) -> tuple[str, int, str]:
    """(label, row, text in the cell) the answer writes to: a blank last week
    first, else this week. Week 1.1's previous row is last year's."""
    if "PREV" in info and not info["PREV"][1] and label != "1.1":
        return (prev_label, *info["PREV"])
    return (label, *info["CUR"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--from-text")
    ap.add_argument("--mark", action="store_true")
    ap.add_argument("--today")
    ap.add_argument("--print-script", action="store_true")
    ap.add_argument("--books", action="store_true")
    ap.add_argument("--book-note", metavar="ID")
    a = ap.parse_args()
    if a.books:
        for b in unreviewed_books(*report_week()):
            print(f"{b['id']}\t{b['title']}\t{b['author']}")
        return
    if a.book_note:
        ok, msg = save_book_notes(a.book_note, a.from_text or "")
        raise SystemExit(0 if ok else msg)
    if a.from_text is None and not (a.show or a.print_script):
        ask_book_notes(*report_week())
    today = datetime.date.fromisoformat(a.today) if a.today else datetime.date.today()
    sun = sunday_of(today)
    label = _bf.week_row_label(sun)
    prev_label = _bf.week_row_label(sun - datetime.timedelta(days=7))

    info = parse_show(osa(show_script(label, prev_label)))
    if "CUR" not in info:
        raise SystemExit(f"no row labelled {label} in {SHEET}!A")
    prev = info.get("PREV", (None, ""))[1]
    label, row, current = target(info, label, prev_label)
    print(f"week {label} → {SHEET}!{COL}{row}  (this week's Sun {sun})")
    print(f"last week ({prev_label}): {prev or '·'}")
    if current:
        print(f"already filled: {current}")
    if a.show:
        return

    text = a.from_text
    if text is None:
        print("\n" + QUESTION + "\n(one thought per line; blank line finishes)")
        lines = []
        while True:
            try:
                l = input("> ")
            except EOFError:
                break
            if not l.strip():
                break
            lines.append(l)
        text = "\n".join(lines)
    text = bulletize(text)
    if not text:
        raise SystemExit("empty answer; nothing written")
    script = write_script(row, text)
    if a.print_script:
        print(script); return
    print(write_answer(label, row, text))
    if a.mark or a.from_text is None:
        r = subprocess.run([sys.executable, DID_FAST, HABIT], capture_output=True, text=True)
        print((r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else "did-fast: no output")
        subprocess.run([sys.executable, DID_FAST, "--refresh-cache"], capture_output=True, text=True)


if __name__ == "__main__":
    main()
