#!/usr/bin/env python3
"""neon-ledger-audit — verify the Neon points ledger and refresh its visible mirror.

The excel-http daemon on ix journals every /append and /write to
~/vault/g245/neon-ledger/YYYY-MM.jsonl with each cell's before/after formula
(see services/excel-http/server.py). This script is the auditor:

  1. Chain replay — for every cell, entry N's before-formula must equal entry
     N-1's after-formula. A break = something wrote to the cell outside the
     daemon (manual edit, stray osascript, sync clobber). Acks reset the
     baseline; fallback entries (daemon was down, before unknown) warn only.
  2. Live check — for cells touched in the last 48h, read the CURRENT formula
     from Excel and compare to the ledger's last after-formula. A mismatch is
     an unjournaled edit that happened since the last daemon write.
  3. Mirror — regenerate the read-only 分ledger sheet in the workbook from the
     JSONL (last MIRROR_ROWS 0分 entries) so the ledger is eyeballable in
     Excel. The sheet is a VIEW: never hand-edit it, it gets rewritten.

Runs hourly on ix via launchd (com.jm.neon-ledger-audit). Report prepends to
~/vault/g245/neon-ledger-audit.md; exit code 1 when errors were found.

Manual-edit blessing:
  neon-ledger-audit.py --ack '0分!R' --date 7/28 --note 'hand-fixed double credit'
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, "/Users/mckay/i446-monorepo/lib")
from neon import excel  # noqa: E402

LEDGER_DIR = os.path.expanduser("~/vault/g245/neon-ledger")
REPORT = os.path.expanduser("~/vault/g245/neon-ledger-audit.md")
MAX_ITEMS = 200  # per report section; the ledger JSONL is the full record
MIRROR_SHEET = "分ledger"
MIRROR_ROWS = 300
WORKBOOK = "Neon分v12.2.xlsx"
BRANCHES = "卯辰巳午未申酉戌亥"


def block_glyph(ts: str) -> str:
    try:
        h = int(ts[11:13])
    except (ValueError, IndexError):
        return ""
    if h < 4 or h > 21:
        return "亥" if h >= 22 else "卯"
    return BRANCHES[(h - 4) // 2]


def ledger_files(now=None):
    now = now or datetime.datetime.now()
    prev = now.replace(day=1) - datetime.timedelta(days=1)
    return [os.path.join(LEDGER_DIR, m.strftime("%Y-%m") + ".jsonl")
            for m in (prev, now)]


def iter_entries(paths):
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue  # torn trailing line from a live append
        except FileNotFoundError:
            continue


def entry_key(e):
    # Row first (2026-10-11): one physical cell was journaled under two keys,
    # '@10/11' by date-addressed writers (block-turnover) and '@r282' by
    # row-addressed ones (ritual -1n), so each key's chain missed the other's
    # writes and logged ~10 false chain breaks a day. The daemon records the
    # resolved row on every entry; date is only the fallback.
    anchor = f"r{e['row']}" if e.get("row") is not None else e.get("date")
    return (e.get("sheet", ""), e.get("col", ""), str(anchor))


# excel-http stripped osascript's leading "\t\t" echo for blank-cell writes
# until d6d2f694 (2026-10-21 09:04), shifting every field left: the ledger
# recorded before=<new value>, after="" (and, before before_value existed,
# after_value=<new formula>). 404 Sep-Oct entries carry that shape; each
# produced two false chain breaks (its own before, and the next write's
# before vs its empty after). Read them as what actually happened: a write
# onto a blank cell. Bounded by date so no post-fix entry is ever rewritten.
STRIP_BUG_UNTIL = "2026-10-22T00:00:00"


def unshift(e):
    """Corrected copy of a strip-bug entry, else the entry unchanged."""
    if (e.get("kind") not in ("write", "append")
            or (e.get("ts") or "") >= STRIP_BUG_UNTIL
            or e.get("after") != "" or e.get("before") in (None, "")
            or str(e.get("value") or "") == ""):
        return e
    if "before_value" in e:
        if e.get("after_value") not in (None, "") or e.get("before_value") in (None, ""):
            return e
        formula, value = e["before_value"], e["before"]
    else:
        if e.get("after_value") in (None, ""):
            return e
        formula, value = e["after_value"], e["before"]
    fixed = dict(e)
    fixed.update(before="", after=formula, after_value=value, unshifted=True)
    if "before_value" in e:
        fixed["before_value"] = ""
    return fixed


def is_replay_restore(e, prev_after):
    """A `replay …` write that put back exactly what the ledger last recorded:
    the cell was clobbered outside the daemon (sync fork / revert) and has
    already been repaired, so the ledger and the cell agree again."""
    return (str(e.get("src") or "").startswith("replay ")
            and e.get("after") is not None and e.get("after") == prev_after)


# do_reconcile / snapshot fill `before` from the daemon's own latest ledger
# entry (latest_entry), not from Excel, so it can't witness an outside edit;
# their `after` IS a fresh Excel read and still advances the chain. Checking
# that before re-flagged every strip-bug cell a second time at the nightly
# 23:55 reconcile (the daemon's latest after was the bugged "").
LEDGER_DERIVED_BEFORE = {"reconcile", "snapshot"}


def replay(entries):
    """→ (errors, warns, last_after {key: (entry, after)}).

    A break on a cell that a LATER ack blesses is downgraded to a warning —
    the ack is the acknowledgment; without this every acked incident would
    alarm on every audit run forever."""
    errors, warns = [], []
    last = {}
    pending = {}  # key → break messages not yet covered by an ack
    for e in entries:
        e = unshift(e)
        key = entry_key(e)
        cell = f"{key[0]}!{key[1]} @{key[2]}"
        prev = last.get(key)
        if e.get("kind") == "ack":
            for msg in pending.pop(key, []):
                warns.append(f"{msg} [acked {e.get('ts')}: {e.get('note')}]")
            last[key] = (e, e.get("after"))
            continue
        if e.get("kind") in LEDGER_DERIVED_BEFORE:
            pass  # before was copied from the ledger, not read from Excel
        elif e.get("before") is None:  # fallback write, daemon was down
            warns.append(f"{e.get('ts')} {cell}: fallback write (before unknown), src={e.get('src')}")
        elif prev is not None and e["before"] != prev[1] and is_replay_restore(e, prev[1]):
            warns.append(f"{e.get('ts')} {cell}: clobbered outside the daemon "
                         f"(`{prev[1]}` → `{e['before']}`), restored by {e.get('src')}")
        elif prev is not None and e["before"] != prev[1]:
            pending.setdefault(key, []).append(
                f"{e.get('ts')} {cell}: chain BREAK — expected before "
                f"`{prev[1]}` but write saw `{e['before']}` (src={e.get('src')})")
        if e.get("after") is not None:
            last[key] = (e, e["after"])
    for msgs in pending.values():
        errors.extend(msgs)
    return errors, warns, last


def live_check(last, hours=48):
    """Compare current Excel formulas to the ledger's last after-formula."""
    errors = []
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    for (sheet, col, anchor), (e, after) in sorted(last.items()):
        if (e.get("ts") or "") < cutoff:
            continue
        kw = {"date": anchor} if not anchor.startswith("r") else {"row": int(anchor[1:])}
        cur = excel.read(sheet, col, **kw)
        if not cur.get("ok"):
            errors.append(f"{sheet}!{col} @{anchor}: live read failed ({cur.get('error')})")
            continue
        if cur.get("formula", "") != after:
            errors.append(
                f"{sheet}!{col} @{anchor}: LIVE formula `{cur.get('formula')}` != ledger "
                f"`{after}` — unjournaled edit; if deliberate: "
                f"neon-ledger-audit.py --ack '{sheet}!{col}' "
                + (f"--date {anchor}" if not anchor.startswith("r") else f"--row {anchor[1:]}")
                + " --note '<why>'")
    return errors


def as_quote(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def refresh_mirror(entries):
    """Rewrite the 分ledger sheet from the newest 0分 entries (one osascript).
    Runs on ix only (local osascript); the sheet is a regenerated view."""
    rows = [e for e in entries if e.get("sheet") == "0分" and e.get("kind") != "read"]
    rows = rows[-MIRROR_ROWS:]
    header = ["ts", "block", "cell", "kind", "delta", "src", "chain", "after"]
    data = [header] + [[
        e.get("ts", ""), block_glyph(e.get("ts", "")),
        f"{e.get('col')}{e.get('row')} ({e.get('date') or ''})",
        e.get("kind", ""), str(e.get("value") or ""),
        str(e.get("src") or ""), e.get("chain", ""),
        (e.get("after") or "")[:250],
    ] for e in reversed(rows)]  # newest first
    as_rows = ",".join("{" + ",".join(as_quote(c) for c in r) + "}" for r in data)
    n = len(data)
    script = f'''
tell application "Microsoft Excel"
    set wb to workbook "{WORKBOOK}"
    try
        set ws to sheet "{MIRROR_SHEET}" of wb
    on error
        set ws to (make new worksheet at end of wb)
        set name of ws to "{MIRROR_SHEET}"
    end try
    try
        set value of range "A1:H2000" of ws to ""
    end try
    set value of range ("A1:H{n}") of ws to {{{as_rows}}}
    return "{n}"
end tell
'''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return f"mirror refresh failed: {r.stderr.strip()}"
    return None


def write_report(errors, warns, n_entries):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    status = "❌ ERRORS" if errors else ("⚠️ warnings" if warns else "✅ clean")
    lines = [f"## {now} — {status} ({n_entries} ledger entries checked, "
             f"{len(errors)} errors, {len(warns)} warnings)", ""]
    items = [f"- ❌ {e}" for e in errors] + [f"- ⚠️ {w}" for w in warns]
    lines += items[:MAX_ITEMS]
    if len(items) > MAX_ITEMS:
        lines.append(f"- … {len(items) - MAX_ITEMS} more; run scripts/neon-ledger-audit.py for the full list")
    lines.append("")
    section = "\n".join(lines)
    try:
        with open(REPORT, encoding="utf-8") as f:
            old = f.read()
    except FileNotFoundError:
        old = "# Neon Ledger Audit\n\nNightly/hourly chain + live checks of ~/vault/g245/neon-ledger/. Newest first.\n\n"
    head, _, tail = old.partition("\n## ")
    # Cap retained history: unbounded prepend grew the report to 14.5 MB by
    # 2026-08-16, which crashes Obsidian's renderer on vault index (found via
    # bisection). Keep the newest MAX_SECTIONS runs; the ledger JSONL remains
    # the full history.
    # 2026-10-11: the cap alone wasn't enough. ~3,000 standing September
    # chain-break errors repeated in every hourly section put 48 runs at
    # 3.8 MB, and each hourly rewrite froze Obsidian again. Only the newest
    # run keeps its item list (capped at MAX_ITEMS); older runs keep just
    # their header line, which carries the counts.
    MAX_SECTIONS = 48
    old_sections = ("## " + tail).split("\n## ") if tail else []
    old_sections = [sec.split("\n", 1)[0].removeprefix("## ") for sec in old_sections]
    kept = "\n".join("## " + h for h in old_sections[:MAX_SECTIONS - 1])
    body = head + "\n" + section + ("\n" + kept if kept else "")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(body)


def do_ack(target, date, row, note, src):
    sheet, _, col = target.partition("!")
    if not sheet or not col or not note:
        sys.exit("usage: --ack 'SHEET!COL' (--date M/D | --row N) --note '<why>'")
    body = {"sheet": sheet, "col": col, "note": note, "src": src or "audit-cli"}
    if date:
        body["date"] = date
    if row:
        body["row"] = int(row)
    out = excel._curl("/ack", body)
    if not out or not out.get("ok"):
        sys.exit(f"ack failed: {out}")
    print(f"acked {sheet}!{col}: baseline now `{out.get('formula')}` (superseded `{out.get('superseded')}`)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror-only", action="store_true")
    ap.add_argument("--no-mirror", action="store_true")
    ap.add_argument("--ack", metavar="SHEET!COL")
    ap.add_argument("--date")
    ap.add_argument("--row")
    ap.add_argument("--note")
    ap.add_argument("--src")
    args = ap.parse_args()

    if args.ack:
        return do_ack(args.ack, args.date, args.row, args.note, args.src)

    entries = list(iter_entries(ledger_files()))
    errors, warns = [], []
    if not args.mirror_only:
        errors, warns, last = replay(entries)
        errors += live_check(last)
        write_report(errors, warns, len(entries))
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        for w in warns:
            print(f"warn: {w}", file=sys.stderr)
    if not args.no_mirror:
        err = refresh_mirror(entries)
        if err:
            print(err, file=sys.stderr)
            errors.append(err)
    print(f"audit: {len(entries)} entries, {len(errors)} errors, {len(warns)} warnings")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
