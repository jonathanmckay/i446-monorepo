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
  sameday.py adopt SHEET!COL VALUE [--date D]
                          accept a hand-typed 0n/hcbi cell: re-set the same value
                          through the daemon (the ledger now has it, Excel is
                          unchanged) and ack the chain break it causes

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

from neon import ledger, rules  # noqa: E402

CARD = "⚠ Neon ledger drift today"
# 0分 point column → the @code a 分 log credits it through (did-fast LABEL_TO_0FEN)
COL_CODE = {"Q": "0g", "R": "i9", "S": "m5x2", "T": "g245", "U": "hcmc",
            "V": "hcm", "W": "hcb", "X": "xk87", "Y": "s897"}

_CELL = re.compile(r"^(0n|hcbi)!([A-Z]+)\d*(?:\((.+?)\))?: Excel (\S+), ledger (\S+)")
HEADERS = Path.home() / ".claude/skills/did/headers.json"
ADOPT = "sameday.py adopt"
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
                # Re-set the same value through the daemon. Not /did: it adds
                # for cumulative habits, closes the card and can credit again.
                v = "''" if cleared else _num(xv)
                what = "cleared" if cleared else f"= {_num(xv)} typed"
                out.append(f"{sheet}!{cell} ({hdr}) {what} in Excel → `{ADOPT} {sheet}!{cell} {v}`")
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
    body = (f"Strict ledger check for {day:%-m/%-d} (22:30): Excel holds things the ledger never saw. "
            "Fix them tonight and the 04:05 check passes. Claim this card with /d and an agent runs "
            f"the `{ADOPT}` lines (tools/neon-ledger/sameday.py):\n"
            + "\n".join(f"- {ln}" for ln in lines))
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


def adopt(target: str, value: str, day: dt.date) -> int:
    """Journal a hand-typed cell as-is: daemon set of the same value + ack."""
    sheet, col = target.split("!", 1)
    if sheet not in ("0n", "hcbi"):
        raise SystemExit("adopt takes 0n!COL or hcbi!COL; a 0分 term is deleted and re-logged as a 分 log")
    v: object = "" if value in ("''", '""', "") else value
    try:
        f = float(v)
        v = int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        pass
    from neon import excel
    md = f"{day.month}/{day.day}"
    r = excel.batch_write(sheet, [{"col": col, "value": v, "mode": "set"}], date=md,
                          src=f"adopt hand edit {target}")
    if not r.get("ok"):
        print(f"ERROR: {r.get('error') or r}")
        return 1
    subprocess.run([sys.executable, str(MONO / "scripts/neon-ledger-audit.py"), "--ack", target,
                    "--date", md, "--note", "adopted hand edit (sameday.py adopt)"],
                   check=False, capture_output=True, timeout=60)
    print(f"adopted {target} = {v!r} on {md}")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "adopt":
        aa = argparse.ArgumentParser(prog="sameday.py adopt")
        aa.add_argument("target"); aa.add_argument("value"); aa.add_argument("--date")
        x = aa.parse_args(sys.argv[2:])
        return adopt(x.target, x.value, dt.date.fromisoformat(x.date) if x.date else dt.date.today())
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    day = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    if not a.date:
        snapshot()
    ledger.catch_up()                # same as neon.py strict: fallback writes, then
    ledger.repair_dates()            # date the row-addressed writes
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
