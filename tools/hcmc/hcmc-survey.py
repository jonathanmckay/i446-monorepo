#!/usr/bin/env python3
"""hcmc-survey — the weekly media check-in behind /1hcmc.

Question: What did I learn about media consumption this week? How can I do better next week?

The answer lives in Neon `1分+1s` column AO (the media "Notes" column, next to the
新闻/词汇/-1/-2/-3/书 counts) on the row of the current Sunday-anchored fiscal week,
the same row `/book finished` bumps for books (col AM). Writes go through ix-osa.sh.

Usage:
  hcmc-survey.py --show                 print last week's and this week's AO notes + the target row
  hcmc-survey.py --from-text "…"        write the answer (one "• " bullet per line) to AO<row>
  hcmc-survey.py --from-text "…" --mark ...and mark the `1 hcmc` habit done (did-fast) + refresh dtd
  hcmc-survey.py                        interactive: show, ask, write, mark
Options: --today YYYY-MM-DD (tests), --print-script (dump the AppleScript instead of running it)
"""
from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("book_finish", os.path.join(os.path.dirname(os.path.abspath(__file__)), "book-finish.py"))
_bf = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_bf)   # reuse week_row_label

IX_OSA = os.path.expanduser("~/.claude/skills/_lib/ix-osa.sh")
DID_FAST = os.path.expanduser("~/i446-monorepo/tools/did/did-fast.py")
WORKBOOK = "Neon分v12.2.xlsx"
SHEET = "1分+1s"
COL = "AO"
HABIT = "1 hcmc"
QUESTION = "What did I learn about media consumption this week? How can I do better next week?"
FIRST_ROW, LAST_ROW = 2, 120


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--from-text")
    ap.add_argument("--mark", action="store_true")
    ap.add_argument("--today")
    ap.add_argument("--print-script", action="store_true")
    a = ap.parse_args()
    today = datetime.date.fromisoformat(a.today) if a.today else datetime.date.today()
    sun = sunday_of(today)
    label = _bf.week_row_label(sun)
    prev_label = _bf.week_row_label(sun - datetime.timedelta(days=7))

    info = parse_show(osa(show_script(label, prev_label)))
    if "CUR" not in info:
        raise SystemExit(f"no row labelled {label} in {SHEET}!A")
    row, current = info["CUR"]
    prev = info.get("PREV", (None, ""))[1]
    print(f"week {label} → {SHEET}!{COL}{row}  (Sun {sun})")
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
    print(osa(script))
    if a.mark or a.from_text is None:
        r = subprocess.run([sys.executable, DID_FAST, HABIT], capture_output=True, text=True)
        print((r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else "did-fast: no output")
        subprocess.run([sys.executable, DID_FAST, "--refresh-cache"], capture_output=True, text=True)


if __name__ == "__main__":
    main()
