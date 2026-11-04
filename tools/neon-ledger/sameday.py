#!/usr/bin/env python3
"""Same-day ledger drift card (Neon off Excel, 2026-11-04).

The strict check (rules.strict_check, run for YESTERDAY at 04:05) fails a day
when Excel holds something the ledger never saw: a habit cell typed into 0n,
a +N typed into a 0分 formula. By then the day is over and the streak is
gone. This runs the same check on TODAY at 22:30, after a fresh snapshot, and
puts one dtd card up with the fix for each miss, while there is still time.

  sameday.py              snapshot, check today, card if anything drifts
  sameday.py --dry-run    snapshot + check, print the card, create nothing
  sameday.py --date D     check D instead of today (no snapshot)

Cron on Ix: 30 22 * * * (config/ix.crontab).
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

MONO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MONO / "lib"))

from neon import rules  # noqa: E402

CARD = "⚠ Neon ledger drift today"
# 0分 point column → the @code a 分 log credits it through (did-fast LABEL_TO_0FEN)
COL_CODE = {"Q": "0g", "R": "i9", "S": "m5x2", "T": "g245", "U": "hcmc",
            "V": "hcm", "W": "hcb", "X": "xk87", "Y": "s897"}

_CELL = re.compile(r"^(0n|hcbi)!([A-Z]+)\d*(?:\((.+?)\))?: Excel (\S+), ledger (\S+)")
HEADERS = Path.home() / ".claude/skills/did/headers.json"
# hcbi cells whose skill SETS the value (re-running it with the same value is
# safe); every other hcbi cell is an /ate append, so re-logging would double it.
HCBI_SET = {"caf": "/caf", "AI": "/out", "ff": "/hiit"}


def _col_num(letter: str) -> int:
    n = 0
    for ch in letter:
        n = n * 26 + ord(ch) - 64
    return n


def did_name(cell_letter: str, header: str) -> str:
    """Shortest /did name that routes to this 0n column (headers.json); the
    raw header can contain '+' that did-fast would read as a bonus."""
    try:
        import json
        names = [k for k, v in json.loads(HEADERS.read_text()).get("0n", {}).items()
                 if v == _col_num(cell_letter)]
    except Exception:  # noqa: BLE001
        names = []
    return min(names, key=len) if names else header
_TYPED = re.compile(r"^([+-]\d+(?:\.\d+)?) in (?:Excel|0分) formula, never journaled")
_GONE = re.compile(r"^([+-]\d+(?:\.\d+)?) journaled (?:to 0分 )?but gone from Excel")


def _num(s: str) -> str:
    try:
        v = float(s)
        return str(int(v)) if v.is_integer() else s
    except ValueError:
        return s


def fixes(misses: list[dict]) -> list[str]:
    """One actionable line per cause in the strict check's misses."""
    out: list[str] = []
    for m in misses:
        col, code = m["col"], COL_CODE.get(m["col"], "?")
        for why in m.get("why") or []:
            why = re.sub(r"^0分!\w+ \(.*?\): ", "", why)
            z = _CELL.match(why)
            t = _TYPED.match(why)
            g = _GONE.match(why)
            if z:
                sheet, cell, hdr, xv, lv = z.groups()
                hdr = " ".join((hdr or cell).replace("\xa0", " ").split())
                cleared = xv in ("—", "None", "blank", "''")
                if sheet == "0n":
                    name = did_name(cell, hdr)
                    out.append(f"0n!{cell} ({hdr}) was cleared in Excel; re-log it or `/did {name} 0`"
                               if cleared else
                               f"0n!{cell} ({hdr}) = {_num(xv)} typed in Excel → `/did {name} {_num(xv)}`")
                elif hdr in HCBI_SET:
                    out.append(f"hcbi!{cell} ({hdr}) = {_num(xv)} typed in Excel → "
                               f"`{HCBI_SET[hdr]} {_num(xv)}`")
                else:
                    out.append(f"hcbi!{cell} ({hdr}) = {_num(xv)} typed in Excel (ledger {lv}) → clear "
                               "the hand-typed part, then re-log it with `/ate` (it adds, so don't "
                               "log on top)")
            elif t:
                n = t.group(1)
                out.append(f"0分!{col} has {n} typed into Excel → delete it from the cell, then "
                           f"`{n} @{code} <what it was for>`")
            elif g:
                n = g.group(1)
                out.append(f"0分!{col} lost a journaled {n} → put {n} back in the cell, or if it "
                           f"was meant to go, `{'-' if not n.startswith('-') else '+'}{n.lstrip('+-')} @{code} undo`")
            elif why.startswith("P (-1₦)"):
                out.append("0分!P (-1₦) was edited by hand → restore it; rituals stamp P")
            else:
                out.append(f"0分!{col} ({m.get('field')}): {why}")
        if not m.get("why"):
            out.append(f"0分!{col} ({m.get('field')}) is off by {m.get('delta')} with no traced cause; "
                       "check the cell's formula")
    return out


def snapshot() -> None:
    subprocess.run([sys.executable, str(MONO / "scripts/neon-reconcile.py")],
                   check=False, capture_output=True, timeout=600)


def post_card(day: dt.date, lines: list[str], dry: bool) -> str:
    body = f"Strict ledger check for {day:%-m/%-d} (22:30). Fix before the 23:55 snapshot:\n" + \
           "\n".join(f"- {ln}" for ln in lines)
    if dry:
        return body
    import todoist
    today = day.isoformat()
    open_ = [t for t in todoist.find_tasks(labels=["i447"], content_contains=CARD)
             if ((t.get("due") or {}).get("date") or "")[:10] == today]
    if open_:
        todoist.add_comment(open_[0]["id"], body)
        return f"updated card {open_[0]['id']}"
    t = todoist.create_task(f"{CARD} (5) [10]", labels=["i447"], due_string="today")
    todoist.add_comment(t["id"], body)
    return f"created card {t['id']}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    day = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    if not a.date:
        snapshot()
    res = rules.strict_check(day.isoformat())
    if res["pass"]:
        print(f"{day}: clean")
        return 0
    lines = fixes(res["misses"])
    if not lines:
        print(f"{day}: incomplete (no observation yet), no card")
        return 0
    print(post_card(day, lines, a.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
