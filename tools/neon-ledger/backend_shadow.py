#!/usr/bin/env python3
"""backend_shadow: would the Neon book have answered this write the way Excel did?

Tails the daemon's journal (~/vault/g245/neon-ledger/YYYY-MM.jsonl). For every
write the daemon applied, it applies the same request to its own copy of the
book (lib/neon/bookops) and compares the four strings Excel returned (formula
and value before, value and formula after) plus the row Excel resolved for the
date. Then it adopts what Excel actually holds, so one miss never cascades.
Everything else in the journal (baselines, snapshots, acks, hand edits the
daemon noticed, client fallback lines) is adopted without judgement.

A separate process on purpose (2026-11-22 critique): no hook in the live
daemon, nothing that can slow or break a write. Read-only towards Excel, the
daemon and the ledger; it owns two files:
  ~/.local/share/neon/backend-shadow.pickle   its book + journal position
  ~/.cache/neon-rules/backend-shadow.jsonl    one line per run + one per miss

  backend_shadow.py init      start from the book seed (lib/neon/book.SEED)
  backend_shadow.py run       judge the journal lines added since the last run
  backend_shadow.py summary   totals by day and by cause

Causes of a miss:
  backend        same starting cell, different result: a bug in bookops/calc
  format         same number, rendered differently
  lookup         the date resolved to a different row
  value          the cell's formula matches but its value does not: a cell it
                 depends on differs (see drift), or Excel answered mid-recalc
  drift          the cell was not what the book held before the write: typed
                 into Excel, written by a raw AppleScript, or a queue replay
                 (reapply/recovered are named when the journal says so)
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from neon import book, bookops, calc, replay  # noqa: E402

STATE = Path(os.environ.get("NEON_SHADOW_STATE") or Path.home() / ".local/share/neon/backend-shadow.pickle")
LOG = Path(os.environ.get("NEON_SHADOW_LOG") or Path.home() / ".cache/neon-rules/backend-shadow.jsonl")
JOURNAL_DIR = Path.home() / "vault/g245/neon-ledger"
MARGIN_S = 300          # journal lines this close before the seed's save are adopted, not judged
NUM_TOL = 1e-9


def _content(after):
    """Journal `after` (the daemon's read-back of the whole cell) → content."""
    if after is None:
        return book._UNKNOWN
    s = str(after)
    if s.strip() == "":
        return None
    if s.startswith("=") and len(s) > 1:
        return s
    return bookops.infer(s)


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _same_value(a, b) -> str:
    """"exact" | "format" (same number, different text) | "" (different)."""
    a, b = ("" if a is None else str(a)), ("" if b is None else str(b))
    if a == b:
        return "exact"
    x, y = _num(a), _num(b)
    if x is not None and y is not None and abs(x - y) <= NUM_TOL * max(1.0, abs(y)):
        return "format"
    return ""


def init(seed_path: Path = None, state: Path = None) -> dict:
    d = book.read_seed(seed_path or book.SEED)
    if d is None:
        raise SystemExit("no book seed; run book_check.py seed first")
    saved = d["meta"]["saved"]
    st = {"cells": dict(d["cells"]), "arrays": d["arrays"], "seed_saved": saved,
          "judge_after": saved, "adopt_after": (dt.datetime.fromisoformat(saved)
                                                - dt.timedelta(seconds=MARGIN_S)).isoformat(timespec="seconds"),
          "pos": {}, "totals": {}}
    _save(st, state or STATE)
    return {"init": saved, "cells": len(st["cells"])}


def _save(st: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    with open(part, "wb") as f:
        pickle.dump(st, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(part, path)


def _load(path: Path):
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except (OSError, pickle.UnpicklingError, EOFError):
        return None


def _new_entries(st: dict, journal_dir: Path):
    """(file name, offset after the line, entry) for complete lines not yet
    seen, files in month order starting at the seed's month."""
    first = st["adopt_after"][:7] + ".jsonl"
    for p in sorted(journal_dir.glob("20??-??.jsonl")):
        if p.name < first:
            continue
        off = st["pos"].get(p.name, 0)
        with open(p, "rb") as f:
            f.seek(off)
            while True:
                line = f.readline()
                if not line or not line.endswith(b"\n"):
                    break                              # a torn last line waits for the next run
                off += len(line)
                try:
                    e = json.loads(line)
                except ValueError:
                    e = None
                yield p.name, off, e


def judge(b: calc.Book, e: dict) -> dict:
    """Apply one daemon write to the book and compare with what Excel
    answered. Mutates `b`; the caller adopts Excel's `after` afterwards.
    → {"cause": None | str, ...detail}."""
    sheet, col, row = e["sheet"], str(e["col"]).upper(), int(e["row"])
    out: dict = {"cause": None}
    if sheet not in b._extent:
        return {"cause": "backend", "what": "unknown sheet"}
    target = e.get("week") or e.get("date")
    if target and sheet in bookops.DATE_COL:
        found = bookops.find_row(b, sheet, target)
        if found != row:
            out.update(cause="lookup", what=f"{target!r}: book row {found}, excel row {row}")
    mode = e.get("mode")
    try:
        before, before_value, after_value, after = bookops.apply(b, sheet, col, row, e["kind"], e.get("value"), mode)
    except Exception as ex:  # noqa: BLE001 — a request the model cannot fold is a finding
        return {"cause": "backend", "what": f"{type(ex).__name__}: {ex}"[:160]}
    xb, xa = e.get("before") or "", e.get("after") or ""
    if not replay.same(before, xb):
        tag = "recovered" if e.get("recovered") else "reapply" if e.get("reapply") else None
        return {"cause": "drift", "what": tag or "cell differed before the write",
                "book_before": before[:80], "excel_before": xb[:80]}
    if not replay.same(after, xa):
        return {"cause": "backend", "what": "result formula", "before": xb[:60], "value": str(e.get("value"))[:40],
                "mode": mode, "book": after[:80], "excel": xa[:80]}
    if out["cause"]:
        return out
    for name, mine, theirs in (("before_value", before_value, e.get("before_value")),
                               ("after_value", after_value, e.get("after_value"))):
        if theirs is None:
            continue
        s = _same_value(mine, theirs)
        if s == "format":
            out = {"cause": "format", "what": name, "book": mine[:40], "excel": str(theirs)[:40]}
        elif s == "":
            return {"cause": "value", "what": name, "book": mine[:40], "excel": str(theirs)[:40], "formula": xa[:60]}
    return out


def run(state: Path = None, journal_dir: Path = None, log: Path = None, today: dt.date = None) -> dict:
    state, journal_dir, log = state or STATE, journal_dir or JOURNAL_DIR, log or LOG
    st = _load(state)
    if st is None:
        raise SystemExit("no shadow state; run: backend_shadow.py init")
    b = calc.Book(st["cells"], today=today or dt.date.today(), arrays=st["arrays"])
    counts: collections.Counter = collections.Counter()
    misses, last_ts = [], None
    for name, off, e in _new_entries(st, journal_dir):
        st["pos"][name] = off
        if not isinstance(e, dict) or not e.get("sheet") or not e.get("col") or not e.get("row"):
            continue
        ts = str(e.get("ts") or "")
        if ts <= st["adopt_after"]:
            continue                                   # already in the seed
        last_ts = ts
        truth = _content(e.get("after"))
        is_write = e.get("kind") in ("write", "append") and "chain" in e
        # The daemon sometimes reads back "" for a write that did land (214
        # journal lines in Oct–Nov): nothing to compare with and nothing to
        # adopt, so the book keeps its own result for that cell.
        blind = is_write and e.get("after") == "" and str(e.get("value") or "") != ""
        if blind:
            counts["blind"] += 1
            try:
                bookops.apply(b, e["sheet"], str(e["col"]).upper(), int(e["row"]), e["kind"], e.get("value"), e.get("mode"))
            except Exception:  # noqa: BLE001
                pass
            continue
        if is_write and ts > st["judge_after"]:
            counts["judged"] += 1
            r = judge(b, e)
            cause = r.pop("cause")
            counts[cause or "clean"] += 1
            if cause:
                misses.append({"ts": ts, "cell": f"{e['sheet']}!{e['col']}{e['row']}", "src": (e.get("src") or "")[:40],
                               "cause": cause, **r})
        else:
            counts["adopted"] += 1
        if truth is not book._UNKNOWN and e["sheet"] in b._extent:
            b.set(e["sheet"], calc.col_index(str(e["col"]).upper()), int(e["row"]), truth)
    st["cells"] = b.cells
    day = (last_ts or dt.datetime.now().isoformat())[:10]
    tot = st["totals"].setdefault(day, {})
    for k, v in counts.items():
        tot[k] = tot.get(k, 0) + v
    _save(st, state)
    summary = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "run": True, "through": last_ts,
               **{k: counts[k] for k in ("judged", "clean", "adopted", "blind", "backend", "format", "lookup", "value", "drift")}}
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as f:
            for m in misses:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
            if counts:
                f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return {**summary, "misses": misses}


def summary(state: Path = None) -> dict:
    st = _load(state or STATE)
    if st is None:
        return {"error": "no shadow state"}
    return {"seed_saved": st["seed_saved"], "days": st["totals"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["init", "run", "summary"])
    a = ap.parse_args(argv)
    out = init() if a.cmd == "init" else run() if a.cmd == "run" else summary()
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if a.cmd != "run" or not (out.get("backend") or out.get("lookup")) else 1


if __name__ == "__main__":
    sys.exit(main())
