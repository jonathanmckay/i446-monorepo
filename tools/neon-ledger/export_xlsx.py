#!/usr/bin/env python3
"""export_xlsx: the ledger's Neon book written back out as a workbook.

2026-11-24, JM: "create a v14 that is a daily back export from the ledger
into neon ... forget about the images, it should just be essentially a
backup." So: Neon分v14-export.xlsx, beside the live workbook, rewritten once
a day from the book (lib/neon/book: the saved workbook's cells + every write the
ledger journaled since). It is an OUTPUT. Nothing reads it and nothing may
write to it; the next run replaces it whole.

The name carries "-export" on purpose. Excel's audit macro (neon-audit
CNeonAudit.cls, IsNeon) and every `Neon分v[\d.]+\.xlsx$` picker in the repo
take a bare "Neon分v14.xlsx" for THE workbook: a cell typed into it would be
journaled as a write to the live one and replayed into the ledger. A hyphen
fails all of them.

How:
  1. copy the saved workbook (never open the file Excel has open) and seed a
     private book from that copy, then replay the ledger onto it: the book is
     exactly "this file + the writes journaled after its save";
  2. open the copy with openpyxl, set every cell whose content the ledger
     changed, save. Number formats, widths and colours survive; pictures and
     the rich-data parts do not (openpyxl drops them, accepted above); formula
     cells carry no cached value, so Excel computes them when the file opens;
  3. gate: read the result back and compare every cell's content with the
     book. Any difference and nothing is published (the previous export stays);
  4. publish with one rename.

  export_xlsx.py                 export to Neon分v14-export.xlsx
  export_xlsx.py --out PATH      somewhere else (tests, dry runs)
  export_xlsx.py status          the last run's log line

Log: ~/.cache/neon-rules/export-v14.jsonl, one line per run. Exit 1 when
nothing was published. Python 3.9-clean (runs on Ix).

Scheduling: a launchd-spawned python on Ix cannot read OneDrive (no Full Disk
Access), an ssh-invoked one can, so the LaunchAgent (com.jm.neon-v14-export,
daily 04:30) runs this over a loopback ssh. See export-v14.sh.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from neon import book, calc, ledger  # noqa: E402

OUT = book.SRC.with_name("Neon分v14-export.xlsx")
LOG = Path(os.environ.get("NEON_EXPORT_LOG") or Path.home() / ".cache/neon-rules/export-v14.jsonl")


def _same(x, y) -> bool:
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) <= 1e-9 * max(1.0, abs(y))
    return x == y


def _cell(k) -> str:
    return "%s!%s%d" % (k[0], calc.col_letter(k[1]), k[2])


def changed_cells(live, seed_cells: dict) -> dict:
    """{(sheet, col, row): content or None} for every cell the ledger's
    replay left different from the seeded workbook (None = cleared)."""
    out = {}
    for k in set(live.cells) | set(seed_cells):
        new = live.cells.get(k)
        if not _same(new, seed_cells.get(k)):
            out[k] = new
    return out


def journaled_since(since_iso: str, db_path=None) -> int:
    """How many writes the ledger holds after `since_iso`, by book.py's own
    rule. Raises on any sqlite error: book.writes_since answers [] on one
    (a busy DB, 1 s timeout), and "the workbook plus zero writes" would then
    pass the gate and be published as the ledger's state."""
    p = Path(db_path or ledger.DB_PATH)
    if not p.exists():
        return 0
    conn = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=30.0)
    try:
        return conn.execute(
            """SELECT COUNT(*) FROM events WHERE ts > ? AND row IS NOT NULL
                  AND (kind IN (%s) OR jsonl_kind = 'excel-edit')""" % ",".join("?" * len(book._WRITE_KINDS)),
            (since_iso,) + tuple(book._WRITE_KINDS)).fetchone()[0]
    finally:
        conn.close()


def apply_changes(xlsx: Path, changes: dict, out: Path) -> None:
    """Set `changes` on a copy of the workbook and save it as `out`."""
    import openpyxl
    from openpyxl.worksheet.formula import ArrayFormula
    wb = openpyxl.load_workbook(xlsx)
    for (sheet, col, row), content in changes.items():
        if sheet not in wb.sheetnames:
            continue
        if isinstance(content, calc.Err):
            content = content.code
        elif isinstance(content, float) and content.is_integer() and abs(content) < 1e15:
            content = int(content)               # 5, not 5.0, as Excel stores it
        cell = wb[sheet].cell(row=row, column=col)
        if isinstance(cell.value, ArrayFormula) and isinstance(content, str) and content.startswith("="):
            content = ArrayFormula(ref=cell.value.ref, text=content)   # an array formula stays one
        cell.value = content
    wb.save(out)


def differences(live, exported: Path, limit: int = 20) -> list:
    """Cells whose content in the exported file is not the book's."""
    got, _cached = calc.load_xlsx(exported, today=live.today)
    bad = [(k, live.cells.get(k), got.cells.get(k))
           for k in set(live.cells) | set(got.cells)
           if not _same(live.cells.get(k), got.cells.get(k))]
    return [{"cell": _cell(k), "book": repr(x)[:80], "file": repr(y)[:80]}
            for k, x, y in sorted(bad, key=lambda t: str(t[0]))[:limit]] if bad else []


def export(src: Path = book.SRC, out: Path = OUT, db_path=None) -> dict:
    t0 = time.time()
    res = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "src": str(src), "out": str(out),
           "published": False}
    tmp = Path(tempfile.mkdtemp(prefix="neon-export-"))
    try:
        copy = tmp / "src.xlsx"
        before = Path(src).stat().st_mtime
        shutil.copy2(src, copy)                  # keeps the mtime: the replay starts from it
        if abs(copy.stat().st_mtime - before) > 1 or abs(Path(src).stat().st_mtime - before) > 1:
            res["error"] = "the workbook was saved while it was being copied"
            return res
        seed_path = tmp / "seed.pickle"          # private: never the shared book.SEED
        meta = book.seed(copy, seed_path)
        live = book.load(seed_path, db_path=db_path, today=dt.date.today())
        if live is None:
            res["error"] = "no book"
            return res
        since = dt.datetime.fromtimestamp(meta["src_mtime"] - book.MARGIN_S).isoformat(timespec="seconds")
        want = journaled_since(since, db_path)
        if live.applied + len(live.unknown) < want:
            res["error"] = "the ledger holds %d writes since %s, the book replayed %d" % (
                want, since, live.applied + len(live.unknown))
            return res
        seed_cells = book.read_seed(seed_path)["cells"]
        changes = changed_cells(live, seed_cells)
        res.update(workbook_saved=meta["saved"], through=live.through, writes_applied=live.applied,
                   cells_changed=len(changes), unknown_writes=len(live.unknown), cells=len(live.cells))
        # A write whose content the journal does not carry cannot be exported;
        # the saved workbook usually has it already. Reported, not fatal: this
        # is a backup, and yesterday's file is a worse one.
        built = tmp / "out.xlsx"
        apply_changes(copy, changes, built)
        diff = differences(live, built)
        if diff:
            res.update(error="exported file differs from the book", differ=diff)
            return res
        out = Path(out)
        part = out.with_name(".neon-export.part")   # a name no Neon picker matches
        shutil.copyfile(built, part)
        os.replace(part, out)
        res.update(published=True, bytes=out.stat().st_size)
        return res
    except Exception as e:  # noqa: BLE001  a failed backup must say why, never half-publish
        res["error"] = "%s: %s" % (type(e).__name__, e)
        return res
    finally:
        res["seconds"] = round(time.time() - t0, 1)
        shutil.rmtree(tmp, ignore_errors=True)


def _log(res: dict) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", nargs="?", default="export", choices=["export", "status"])
    ap.add_argument("--src", type=Path, default=book.SRC)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    if args.cmd == "status":
        try:
            print(LOG.read_text(encoding="utf-8").strip().splitlines()[-1])
        except (OSError, IndexError):
            print("no export has run")
        return 0
    if args.out.resolve() == args.src.resolve():
        print("refusing to export over the source workbook", file=sys.stderr)
        return 1
    res = export(args.src, args.out)
    _log(res)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["published"] else 1


if __name__ == "__main__":
    sys.exit(main())
