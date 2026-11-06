#!/usr/bin/env python3
"""Fill Neon 0n!AG (⎣∀clr, HHMM all colors was reached), 0n!AH (#, handicap)
and 0n!AF (N color, HHMM both 0t and 0l were done).

  allcolors.py --nightly   launchd on ix, 00:15: settle every closed day since
                           the last filled AH (stamp AG from ledger timestamps;
                           writes after the day ended do not count) and fill
                           AH forward to today; stamp yesterday's AF if a live
                           run missed it.
  allcolors.py --live      spawned by did-fast after a same-day 0n/1n+ write:
                           same roll, then stamp today's AG with the ledger
                           time the threshold was crossed, if it has been, and
                           today's AF once 0t and 0l are both done.
  --date YYYY-MM-DD        act as if today were this date (backfill/testing)
  --dry-run                print, write nothing

A manual AG (/allcolors) is never overwritten. AH is only written when blank,
and AF only when blank, so reruns are idempotent. Rule: lib/neon/allcolors.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import os
import socket
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lib"))

from neon import allcolors as ac  # noqa: E402
from neon import cols, excel, ledger, weeks  # noqa: E402
import daytime  # noqa: E402

SHEET = "0n"
FIRST_COL, LAST_COL = "D", "AS"
LOCK = Path.home() / ".cache/jm/allcolors.lock"
SRC = "allcolors auto"


def md(d: dt.date) -> str:
    return f"{d.month}/{d.day}"


def col_index(letter: str) -> int:
    n = 0
    for ch in letter:
        n = n * 26 + ord(ch) - 64
    return n


def _rows(sheet: str, rng: str) -> list[list[str]]:
    r = excel.read_range(sheet, rng)
    if not r.get("ok"):
        raise RuntimeError(f"read {sheet}!{rng}: {r.get('error')}")
    return r.get("rows") or []


class Day:
    """0n row of one date: header → (col letter, raw value)."""

    def __init__(self, d: dt.date, headers: list[str]):
        self.date = d
        self.row = excel.lookup_row(SHEET, md(d))
        if not self.row:
            raise RuntimeError(f"no 0n row for {md(d)}")
        vals = _rows(SHEET, f"{FIRST_COL}{self.row}:{LAST_COL}{self.row}")[0]
        base = col_index(FIRST_COL)
        self.cells = {}
        for i, h in enumerate(headers):
            letter = cols.col_letter(base + i)
            self.cells[letter] = (h, vals[i] if i < len(vals) else "")

    def raw(self, header: str) -> str:
        for h, v in self.cells.values():
            if ac.norm(h) == ac.norm(header):
                return v
        raise KeyError(header)


# ── completion times from the semantic ledger ────────────────────────────────

def _done_at(conn: sqlite3.Connection, sheet: str, col: str, row: int):
    """When the cell's current non-blank run began, per the ledger: the first
    write after the last blank-value clear (ctrl-z). Cells never written
    through the daemon (typed in Excel) fall back to the first `observe`
    snapshot that saw a value. None = no trace at all."""
    q = ("SELECT ts, kind, value, after_value, after_formula FROM events "
         "WHERE sheet=? AND col=? AND row=? ORDER BY ts, id")
    start = seen = None
    for ts, kind, value, after_value, after_formula in conn.execute(q, (sheet, col, row)):
        if kind == "observe":
            if seen is None and (after_value is not None or (after_formula or "").strip()):
                seen = ts
            continue
        if (value is None or str(value).strip() == "") and kind in ("text", "set"):
            start = None
        elif start is None:
            start = ts
    ts = start or seen
    return ledger.parse_ts(ts) if ts else None


def _timed(name: str, raw, at: dt.datetime | None, day: dt.date) -> ac.Cell:
    """Cell as of `day`: a done cell first written after `day` ended is a
    miss (JM: a miss is a miss); one written before `day` (1n+ cards done
    earlier in the week) counts from 00:00."""
    status = ac.classify_value(raw)
    if status != ac.DONE or at is None:
        return ac.Cell(name, status)
    if at.date() > day:
        return ac.Cell(name, ac.BLANK)
    if at.date() < day:
        return ac.Cell(name, ac.DONE, dt.datetime.combine(day, dt.time(0, 0)))
    return ac.Cell(name, ac.DONE, at)


def gather(d: dt.date, headers: list[str], conn):
    """(Day, core cells, habit cells incl. virtual 1n) for date `d`."""
    day = Day(d, headers)
    core, habits = [], []
    core_names = {ac.norm(c) for c in ac.CORE}
    for letter, (h, v) in day.cells.items():
        if not ac.is_habit_header(h):
            continue
        cell = _timed(h.strip(), v, _done_at(conn, SHEET, letter, day.row), d)
        (core if ac.norm(h) in core_names else habits).append(cell)
    habits.append(onen_for(d, conn))
    return day, core, habits


def onen_for(d: dt.date, conn) -> ac.Cell:
    """The virtual '1n' habit: all 1n+ cards due on d's weekday."""
    head = _rows("1n+", "C1:AL3")
    names, dows = head[0], head[2]
    want = (d.weekday() + 1) % 7 + 1          # sheet: 1=Sunday … 7=Saturday
    labels = _rows("1n+", "B1:B80")
    label_row = {r[0].strip(): i + 1 for i, r in enumerate(labels) if r and r[0].strip()}
    base = col_index("C")
    due = []
    for i, (name, dow) in enumerate(zip(names, dows)):
        try:
            if int(float(dow)) == want:
                due.append((cols.col_letter(base + i), name))
        except (TypeError, ValueError):
            continue
    cards = []
    week_vals: dict[int, list[str]] = {}
    for letter, name in due:
        label = weeks.habit_week_label(name, d)   # raw header: cycle keys on it
        row = label_row.get(label)
        if not row:
            raise RuntimeError(f"1n+ has no week row {label}")
        if row not in week_vals:
            week_vals[row] = _rows("1n+", f"C{row}:AL{row}")[0]
        vals = week_vals[row]
        idx = col_index(letter) - base
        raw = vals[idx] if idx < len(vals) else ""
        cell = _timed(name.strip(), raw, _done_at(conn, "1n+", letter, row), d)
        if cell.status == ac.BLANK:
            prev_row = label_row.get(weeks.prev_habit_week_label(name, d))
            if prev_row:
                if prev_row not in week_vals:
                    week_vals[prev_row] = _rows("1n+", f"C{prev_row}:AL{prev_row}")[0]
                prev_vals = week_vals[prev_row]
                prev_raw = prev_vals[idx] if idx < len(prev_vals) else ""
                cell = ac.caught_up(cell, prev_raw, _done_at(conn, "1n+", letter, prev_row),
                                    weeks.cycle_start(name, d), d)
        cards.append(cell)
    return ac.onen_cell(cards)


# ── actions ──────────────────────────────────────────────────────────────────

def write(d: dt.date, col: str, value: int, why: str, dry: bool) -> None:
    print(f"{md(d)} {col} ← {value}  ({why})")
    if dry:
        return
    r = excel.batch_write(SHEET, [{"col": col, "value": value}], date=md(d), src=f"{SRC}: {why}")
    if not r.get("ok"):
        raise RuntimeError(f"write {col} {md(d)}: {r.get('error') or r}")


def settle(d: dt.date, headers: list[str], conn, dry: bool,
           unknown: dt.datetime | None = None, ah_override=None) -> tuple:
    """Stamp d's AG if blank and the rule says it was earned. Returns
    (AG, AH) as they stand after the stamp. `ah_override` carries an AH a
    dry run computed but did not write."""
    day, core, habits = gather(d, headers, conn)
    ag, ah = day.raw("⎣∀clr"), day.raw("#")
    if ah_override is not None and ac.classify_value(ah) == ac.BLANK:
        ah = ah_override
    if ac.classify_value(ag) != ac.BLANK:
        return ag, ah
    hit = ac.evaluate(d, core, habits, _int(ah), unknown=unknown)
    misses = [c.name for c in habits if c.status == ac.BLANK]
    core_left = [c.name for c in core if c.status != ac.DONE]
    print(f"{md(d)} AH={_int(ah)} misses={len(misses)} {misses} core_left={core_left} → "
          f"{'AG ' + str(ac.hhmm(hit)) if hit else 'not all colors'}")
    if hit:
        ag = ac.hhmm(hit)
        write(d, "AG", ag, "live" if unknown else "nightly backstop", dry)
    return ag, ah


def settle_n_color(d: dt.date, headers: list[str], conn, dry: bool,
                   unknown: dt.datetime | None = None) -> None:
    """Stamp d's AF if blank and both 0t and 0l were done on d. Never moves an
    existing stamp, so re-logging 0l can't drag it later."""
    day = Day(d, headers)
    if ac.classify_value(day.raw("N color")) != ac.BLANK:
        return
    found = {ac.norm(h): (letter, v) for letter, (h, v) in day.cells.items()}
    t, l = (_timed(n, found[n][1], _done_at(conn, SHEET, found[n][0], day.row), d)
            for n in ("0t", "0l"))
    hit = ac.n_color_time(t, l, unknown=unknown)
    if hit:
        # Ledger times are Ix's clock; AF is the traveler's (/travel override).
        write(d, "AF", ac.hhmm(_local(hit)), "N color: 0t and 0l done", dry)


def _local(t: dt.datetime) -> dt.datetime:
    return t.astimezone(daytime.local_now().tzinfo)


def roll(today: dt.date, headers: list[str], conn, dry: bool, lookback: int = 14) -> None:
    """Settle every closed day since the last filled AH (at least yesterday)
    and fill AH forward to today, so a missed nightly run self-heals."""
    start = None
    for back in range(1, lookback + 1):
        d = today - dt.timedelta(days=back)
        if ac.classify_value(Day(d, headers).raw("#")) != ac.BLANK:
            start = d
            break
    if start is None:
        raise RuntimeError(f"no AH in the last {lookback} days; seed one by hand")
    d, carried = start, None
    while d < today:
        ag, ah = settle(d, headers, conn, dry, ah_override=carried)
        nxt = d + dt.timedelta(days=1)
        carried = None
        if ac.classify_value(Day(nxt, headers).raw("#")) == ac.BLANK:
            carried = ac.next_handicap(ah, ag)
            write(nxt, "AH", carried, f"handicap after {md(d)}", dry)
        d = nxt
    return carried


def _int(v) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 0


def _on_ix() -> bool:
    h = socket.gethostname().lower()
    return h in ("ix", "ix.local")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    m = p.add_mutually_exclusive_group(required=True)
    m.add_argument("--nightly", action="store_true")
    m.add_argument("--live", action="store_true")
    p.add_argument("--date", help="YYYY-MM-DD to treat as today")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    if not _on_ix():
        # The ledger lives on ix; run there.
        args = ["ssh", "ix", "/usr/bin/python3", "~/i446-monorepo/tools/allcolors/allcolors.py"] + sys.argv[1:]
        os.execvp("ssh", args)

    today = dt.date.fromisoformat(a.date) if a.date else daytime.today()
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        headers = _rows(SHEET, f"{FIRST_COL}1:{LAST_COL}1")[0]
        conn = ledger.connect()
        try:
            if a.live:
                settle_n_color(today, headers, conn, a.dry_run, unknown=dt.datetime.now())
            elif a.nightly:
                settle_n_color(today - dt.timedelta(days=1), headers, conn, a.dry_run)
            carried = roll(today, headers, conn, a.dry_run)
            if a.live and not a.date:
                settle(today, headers, conn, a.dry_run,
                       unknown=daytime.local_now(), ah_override=carried)
        finally:
            conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print(f"allcolors: {e}", file=sys.stderr)
        sys.exit(1)
