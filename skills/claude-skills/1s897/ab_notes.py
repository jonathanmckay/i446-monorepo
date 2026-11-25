#!/usr/bin/env python3
"""Merge /1s897's two voices into one 0s897!AB cell without clobbering either.

AB ("Notes for Improvement / Upcoming") holds JM's own notes as `• ` lines
and the bot's commentary as `😈 ` lines (2026-11-25: JM no longer opens Excel,
so the prefix is how he tells them apart wherever the cell is shown).

    ab_notes.py --date 2026-11-18 --show
    ab_notes.py --date 2026-11-18 --bot @/tmp/1s897-ab.txt   # replace 😈 lines only
    ab_notes.py --date 2026-11-18 --jm  @/tmp/1s897-jm.txt   # replace JM's lines only

Each write reads the cell first and keeps the other voice's lines, then goes
through scripts/neon-review-write.py (journaled, row found by date, Z merge
checked). JM's lines always sit above the 😈 lines. Exit 1 with nothing
written on any read or write failure.

A row written before 2026-11-25 has bot bullets with a `• ` prefix; they read
as JM's here and are kept, never replaced.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import re
import sys
import tempfile
from pathlib import Path

SHEET = "0s897"
COL = "AB"
MERGE_COL = "Z"
SRC = "1s897"
DEMON = "😈"
NRW = Path.home() / "i446-monorepo" / "scripts" / "neon-review-write.py"
# A leading bullet, dash, star, demon or "1." that the line's voice replaces.
MARK_RE = re.compile(r"^\s*(?:[•\-*]|" + DEMON + r"|\d+[.)])\s*")


def _clean(text: str) -> list[str]:
    out = []
    for line in (text or "").splitlines():
        body = MARK_RE.sub("", line).strip()
        if body:
            out.append(body)
    return out


def split(cell: str) -> tuple[list[str], list[str]]:
    """(JM's lines, bot's lines) as stored, blank lines dropped."""
    jm, bot = [], []
    for line in (cell or "").splitlines():
        if not line.strip():
            continue
        (bot if line.lstrip().startswith(DEMON) else jm).append(line.strip())
    return jm, bot


def merge(cell: str, *, bot: str | None = None, jm: str | None = None) -> str:
    """The new cell text. A voice passed as None keeps its existing lines."""
    jm_old, bot_old = split(cell)
    jm_new = jm_old if jm is None else [f"• {x}" for x in _clean(jm)]
    bot_new = bot_old if bot is None else [f"{DEMON} {x}" for x in _clean(bot)]
    return "\n".join(jm_new + bot_new)


def _nrw():
    spec = importlib.util.spec_from_file_location("neon_review_write", NRW)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _arg_text(spec: str) -> str:
    return Path(spec[1:]).expanduser().read_text() if spec.startswith("@") else spec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="merge JM's and the bot's lines in 0s897!AB")
    ap.add_argument("--date", required=True, help="week_start (the Wednesday), YYYY-MM-DD")
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--bot", help="text or @file: replaces the 😈 lines")
    who.add_argument("--jm", help="text or @file: replaces JM's lines")
    who.add_argument("--show", action="store_true", help="print the cell and exit")
    ap.add_argument("--dry-run", action="store_true", help="print the merged cell, write nothing")
    a = ap.parse_args(argv)

    nrw = _nrw()
    key = nrw.mdy(dt.date.fromisoformat(a.date))
    r = nrw.excel.lookup(SHEET, key)
    if not (r and r.get("ok") and r.get("row")):
        why = "daemon unreachable" if r is None else r.get("error")
        print(f"ERROR: could not find {key} in {SHEET} ({why}). Nothing written.", file=sys.stderr)
        return 1
    row = r["row"]
    got = nrw.excel.read(SHEET, COL, row=row)
    if not got.get("ok"):
        print(f"ERROR: could not read {SHEET}!{COL}{row}: {got.get('error') or got}. "
              f"Nothing written.", file=sys.stderr)
        return 1
    cell = str(got.get("value") or "")
    if a.show:
        print(cell)
        return 0

    try:
        new = merge(cell, bot=_arg_text(a.bot) if a.bot is not None else None,
                    jm=_arg_text(a.jm) if a.jm is not None else None)
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if not new:
        print("ERROR: the merged cell would be empty. Nothing written.", file=sys.stderr)
        return 1
    if a.dry_run:
        print(new)
        return 0
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(new)
    rc = nrw.main(["--sheet", SHEET, "--date", a.date, "--merge-col", MERGE_COL,
                   "--src", SRC, f"{COL}=@{f.name}"])
    Path(f.name).unlink(missing_ok=True)
    if rc != 0:
        return rc
    back = nrw.excel.read(SHEET, COL, row=row)
    if str(back.get("value") or "") != new:
        print(f"ERROR: {SHEET}!{COL}{row} does not read back as written. Cell now holds:\n"
              f"{back.get('value')}", file=sys.stderr)
        return 1
    print(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
