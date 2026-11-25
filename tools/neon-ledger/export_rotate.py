#!/usr/bin/env python3
"""export_rotate.py: keep dated copies of Neon分v14-export.xlsx (2026-11-25).

export-v14.sh runs this after every successful export_xlsx.py (each block
boundary). It copies the fresh export into `v14-export-snapshots/` beside it as
`Neon分v14-export-YYYYMMDD-HHMM.xlsx`, then deletes every snapshot the policy
does not keep (JM, 2026-11-25):

  blocks   the 7 newest snapshots
  days     the last snapshot of each of the 2 most recent days before today
  7 day    one snapshot that is 7 to 13 days old
  30 day   one snapshot that is 30 to 59 days old

The two long tiers are bucketed by day number (ordinal // 7 and // 30): the
first day with a snapshot in the current bucket and in the previous bucket is
kept, at that day's last snapshot. The previous bucket's is the 7 / 30 day
copy; the current bucket's is next period's, kept now so it exists then. That
needs no state file and survives days on which nothing ran.

The hyphen after v14 is deliberate, as in the export's own name: every
`Neon分v[\\d.]+\\.xlsx$` picker would otherwise take a snapshot for the live
workbook. Python 3.9-clean (runs on Ix).
"""
from __future__ import annotations

import datetime as _dt
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_xlsx  # noqa: E402

DIR_NAME = "v14-export-snapshots"
NAME = re.compile(r"^Neon分v14-export-(\d{8})-(\d{4})\.xlsx$")
KEEP_BLOCKS = 7
KEEP_DAYS = 2
TIERS = (7, 30)


def stamp_of(name: str):
    m = NAME.match(name)
    if not m:
        return None
    try:
        return _dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M")
    except ValueError:
        return None


def keep(names, today: _dt.date) -> set:
    """The subset of snapshot file names the policy keeps. Names that are not
    snapshots are never returned (and never deleted by prune)."""
    snaps = sorted((s, n) for s, n in ((stamp_of(n), n) for n in names) if s is not None)
    out = {n for _, n in snaps[-KEEP_BLOCKS:]}
    last_of_day: dict = {}
    for s, n in snaps:                      # ascending: the last one of a day wins
        last_of_day[s.date()] = n
    before = sorted(d for d in last_of_day if d < today)
    out.update(last_of_day[d] for d in before[-KEEP_DAYS:])
    for span in TIERS:
        cur = today.toordinal() // span
        for bucket in (cur, cur - 1):
            days = sorted(d for d in last_of_day if d.toordinal() // span == bucket)
            if days:
                out.add(last_of_day[days[0]])
    return out


def rotate(export: Path = export_xlsx.OUT, now: _dt.datetime | None = None) -> dict:
    now = now or _dt.datetime.now()
    snapdir = export.parent / DIR_NAME
    snapdir.mkdir(exist_ok=True)
    target = snapdir / f"Neon分v14-export-{now:%Y%m%d-%H%M}.xlsx"
    tmp = target.with_suffix(".tmp")
    shutil.copy2(export, tmp)
    tmp.replace(target)
    names = [p.name for p in snapdir.iterdir() if NAME.match(p.name)]
    kept = keep(names, now.date())
    removed = sorted(n for n in names if n not in kept)
    for n in removed:
        (snapdir / n).unlink()
    return {"snapshot": target.name, "kept": len(kept), "removed": removed}


def main() -> int:
    if not export_xlsx.OUT.exists():
        print(f"no export at {export_xlsx.OUT}", file=sys.stderr)
        return 1
    res = rotate()
    print(f"snapshot {res['snapshot']}: kept {res['kept']}, removed {len(res['removed'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
