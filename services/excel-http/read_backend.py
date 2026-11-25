"""excel-http reads answered from the Neon book (Neon off Excel, 2026-11-25).

`answer(path, req)` builds the reply the daemon's /read, /read_range and
/lookup handlers build from AppleScript, from lib/neon/book.load() instead:
the seed plus every journaled write, evaluated by neon.calc. Loading takes
about 20 ms, so each request gets a fresh book and there is no in-process
state to drift.

Two uses, chosen by NEON_READ_BACKEND when the daemon starts:

  shadow   Excel still answers. After each successful read the daemon calls
           Shadow.judge(), which answers the same request from the book and
           records any difference. Called inside EXCEL_LOCK, where every write
           Excel holds has already been journaled, so the two are compared at
           the same point in the write order. Never changes a reply, never
           raises.
  book     (not wired yet) the reply itself comes from answer().

A difference is one of:
  drift    /read: the cell's content differs. Something changed Excel without
           the journal (typed in, a raw AppleScript write) since the last seed.
  value    same content, another value: a formula the book computes
           differently, or a cell it depends on has drifted.
  format   the same number in another text form.
  lookup   the date or week label resolved to another row.
  range    /read_range: one or more cells differ (examples carried).
  nobook   there is no book seed on this host.

State: counts by day in ~/.cache/neon-rules/read-shadow-counts.json, one line
per distinct difference in ~/.cache/neon-rules/read-shadow.jsonl.
Python 3.9-clean (runs on Ix).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import time
from typing import Optional

from neon import book, bookops, calc, replay

LOG = os.path.expanduser("~/.cache/neon-rules/read-shadow.jsonl")
COUNTS = os.path.expanduser("~/.cache/neon-rules/read-shadow-counts.json")
PATHS = ("/read", "/read_range", "/lookup")
MAX_RANGE_CELLS = 20000
NUM_TOL = 1e-9
FLUSH_S = 60
SEEN_MAX = 5000
EXAMPLES = 5


def answer(path: str, req: dict, b: Optional[calc.Book] = None) -> dict:
    """The handler's reply, from the book. {"ok": False, "error": "no_book"}
    when there is no seed."""
    if b is None:
        b = book.load()
    if b is None:
        return {"ok": False, "error": "no_book"}
    sheet = req.get("sheet")
    if path == "/lookup":
        date_str = req.get("date")
        if not sheet or not date_str:
            return {"ok": False, "error": "missing_sheet_or_date"}
        r = bookops.find_row(b, sheet, date_str)
        return {"ok": True, "row": r} if r else {"ok": False, "error": "date_not_found"}
    if path == "/read":
        col = req.get("col")
        row = None
        if sheet and col:
            if req.get("row"):
                row = int(req["row"])
            elif req.get("date"):
                row = bookops.find_row(b, sheet, req["date"])
        if not row:
            return {"ok": False, "error": "date_not_found_or_missing_target"}
        value, formula = bookops.read(b, sheet, str(col), row)
        return {"ok": True, "row": row, "col": col, "value": value, "formula": formula}
    if path == "/read_range":
        rng = str(req.get("range") or "").upper()
        m = bookops._RANGE.match(rng)
        if not sheet or not m:
            return {"ok": False, "error": "missing_sheet_or_bad_range"}
        c1, r1, c2, r2 = calc.col_index(m.group(1)), int(m.group(2)), calc.col_index(m.group(3)), int(m.group(4))
        if r2 < r1 or c2 < c1:
            return {"ok": False, "error": "bad_range"}
        if (r2 - r1 + 1) * (c2 - c1 + 1) > MAX_RANGE_CELLS:
            return {"ok": False, "error": "range_too_large"}
        return {"ok": True, "range": rng, "row": r1, "rows": bookops.read_range(b, sheet, rng)}
    return {"ok": False, "error": "not_a_read"}


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _same_text(mine, theirs) -> str:
    """"exact" | "format" | ""."""
    mine, theirs = ("" if mine is None else str(mine)), ("" if theirs is None else str(theirs))
    if mine == theirs:
        return "exact"
    x, y = _num(mine), _num(theirs)
    if x is not None and y is not None and abs(x - y) <= NUM_TOL * max(1.0, abs(y)):
        return "format"
    return ""


def compare(path: str, excel: dict, mine: dict) -> Optional[dict]:
    """None when the book's reply equals Excel's, else {"cause", ...detail}."""
    if not mine.get("ok"):
        if mine.get("error") == "no_book":
            return {"cause": "nobook"}
        return {"cause": "lookup", "book": mine.get("error"), "excel": "row %s" % excel.get("row")}
    if path == "/lookup":
        return None if mine["row"] == excel.get("row") else {
            "cause": "lookup", "book": mine["row"], "excel": excel.get("row")}
    if path == "/read":
        if mine["row"] != excel.get("row"):
            return {"cause": "lookup", "book": mine["row"], "excel": excel.get("row")}
        xf, xv = excel.get("formula") or "", excel.get("value")
        if not replay.same(mine["formula"], xf):
            return {"cause": "drift", "book": mine["formula"][:80], "excel": xf[:80]}
        s = _same_text(mine["value"], xv)
        if s == "exact":
            return None
        return {"cause": "format" if s else "value", "book": str(mine["value"])[:60], "excel": str(xv)[:60],
                "formula": xf[:60]}
    theirs, rows = excel.get("rows") or [], mine["rows"]
    if len(rows) != len(theirs) or any(len(a) != len(b) for a, b in zip(rows, theirs)):
        return {"cause": "range", "what": "shape", "book": len(rows), "excel": len(theirs)}
    m = bookops._RANGE.match(mine["range"])
    c1, r1 = calc.col_index(m.group(1)), int(m.group(2))
    bad, ex = 0, []
    for i, (ra, rb) in enumerate(zip(rows, theirs)):
        for j, (a, b) in enumerate(zip(ra, rb)):
            if a != b:
                bad += 1
                if len(ex) < EXAMPLES:
                    ex.append({"cell": "%s%d" % (calc.col_letter(c1 + j), r1 + i), "book": a[:40], "excel": b[:40],
                               "kind": "format" if _same_text(a, b) else "differ"})
    return {"cause": "range", "cells": bad, "examples": ex} if bad else None


class Shadow:
    """Counts and logs for shadow mode. One instance per daemon."""

    def __init__(self, log: str = LOG, counts: str = COUNTS, load=book.load):
        self.log, self.counts_path, self.load = log, counts, load
        self.lock = threading.Lock()
        self.counts: dict = {}
        self.seen: set = set()
        self.flushed: Optional[float] = None       # None: write the counts on the first judge
        try:
            with open(counts) as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.counts = d
        except (OSError, ValueError):
            pass

    def judge(self, path: str, req: dict, excel_reply: dict) -> Optional[dict]:
        """Compare one successful Excel read with the book. → the difference
        or None. Never raises."""
        try:
            if path not in PATHS or not isinstance(excel_reply, dict) or not excel_reply.get("ok"):
                return None
            t0 = time.monotonic()
            try:
                b = self.load()
                diff = {"cause": "nobook"} if b is None else compare(path, excel_reply, answer(path, req, b))
            except Exception as e:  # noqa: BLE001 — a request the book cannot answer is a finding
                diff = {"cause": "error", "what": ("%s: %s" % (type(e).__name__, e))[:160]}
            ms = int((time.monotonic() - t0) * 1000)
            cause = diff["cause"] if diff else "clean"
            day = _dt.date.today().isoformat()
            with self.lock:
                c = self.counts.setdefault(day, {})
                c["judged"] = c.get("judged", 0) + 1
                c[cause] = c.get(cause, 0) + 1
                c["ms_max"] = max(c.get("ms_max", 0), ms)
                if diff:
                    self._record(path, req, diff)
                if self.flushed is None or time.monotonic() - self.flushed > FLUSH_S:
                    self._flush()
            return diff
        except Exception:  # noqa: BLE001 — the shadow must never reach the request path
            return None

    def _record(self, path: str, req: dict, diff: dict) -> None:
        where = req.get("range") or "%s@%s" % (req.get("col") or "", req.get("row") or req.get("date") or "")
        key = (path, req.get("sheet"), where, json.dumps(diff, ensure_ascii=False, sort_keys=True))
        if key in self.seen:
            return                                # a poller repeats the same difference every 45 s
        if len(self.seen) >= SEEN_MAX:
            self.seen.clear()
        self.seen.add(key)
        try:
            os.makedirs(os.path.dirname(self.log), exist_ok=True)
            with open(self.log, "a") as f:
                f.write(json.dumps({"ts": _dt.datetime.now().isoformat(timespec="seconds"), "path": path,
                                    "sheet": req.get("sheet"), "where": where, **diff}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _flush(self) -> None:
        self.flushed = time.monotonic()
        for d in sorted(self.counts)[:-14]:       # two weeks of days is plenty
            del self.counts[d]
        try:
            os.makedirs(os.path.dirname(self.counts_path), exist_ok=True)
            part = self.counts_path + ".part"
            with open(part, "w") as f:
                json.dump(self.counts, f, ensure_ascii=False)
            os.replace(part, self.counts_path)
        except OSError:
            pass

    def state(self) -> dict:
        with self.lock:
            return {"mode": "shadow", "today": dict(self.counts.get(_dt.date.today().isoformat()) or {})}
