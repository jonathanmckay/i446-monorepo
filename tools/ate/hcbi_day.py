#!/usr/bin/env python3
"""hcbi_day.py — set one hcbi cell on the day the text names. Shared by
/out (AI, outdoor score) and /hiit (AE `ff`, future-fit workouts), 2026-11-03.

    <tool>.py "<number> [when]"     write, print one line
    <tool>.py --parse "<text>"      print {"value", "date"} and write nothing

Same text grammar as /caf (tools/ate/caf.py: one signed number, the rest is
the date phrase, none = today). The cell is SET, not accumulated; the
confirmation shows what it replaced. Column from config/neon-cols.json.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import caf  # noqa: E402


class Cell:
    """One hcbi column: `label` names it in output/src, `header` is its
    neon-cols.json key, `noise` are words to drop before parsing."""

    def __init__(self, label: str, header: str, noise: str):
        self.label, self.header = label, header
        self.noise = re.compile(rf"\b({noise})\b", re.I)

    def parse(self, text: str, today: dt.date | None = None) -> dict:
        p = caf.parse(self.noise.sub(" ", text), today)
        return {"value": p["points"], "date": p["date"]}

    def write(self, text: str, today: dt.date | None = None, excel=None, cols=None) -> dict:
        return _write(self, text, today, excel, cols)

    def fmt(self, out: dict) -> str:
        if not out.get("ok"):
            return f"ERROR: {out.get('error')} ({out.get('date', '?')})"
        was = f", was {out['before']}" if out.get("before") is not None else ""
        return f"{self.label} → {out['value']} ({out['date']}, hcbi {out['col']}{out['row']}{was})"

    def main(self, argv: list[str], doc: str) -> int:
        if not argv or argv[0] in ("-h", "--help"):
            print(doc.strip()); return 2
        if argv[0] == "--parse":
            try:
                p = self.parse(" ".join(argv[1:]))
            except ValueError as e:
                print(json.dumps({"ok": False, "error": str(e)})); return 1
            print(json.dumps({"ok": True, "value": p["value"], "date": p["date"].isoformat()}))
            return 0
        try:
            out = self.write(" ".join(argv))
        except ValueError as e:
            print(f"ERROR: {e}"); return 1
        print(self.fmt(out))
        return 0 if out.get("ok") else 3


def _write(cell: Cell, text: str, today, excel, cols) -> dict:
    p = cell.parse(text, today)
    if excel is None or cols is None:
        from neon import excel as _excel, cols as _cols
        excel, cols = excel or _excel, cols or _cols
    col = cols.col("hcbi", cell.header)
    d = p["date"]
    v = int(p["value"]) if p["value"] == int(p["value"]) else p["value"]
    r = excel.batch_write("hcbi", [{"col": col, "value": v, "mode": "set"}],
                          date=f"{d.month}/{d.day}", src=f"{cell.label} {v} {d.isoformat()}")
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error") or r, "date": d.isoformat()}
    res = (r.get("results") or [{}])[0]
    before = (res.get("before_value") or "").strip()
    if before == "missing value":
        before = ""
    return {"ok": True, "value": v, "date": d.isoformat(), "col": col, "row": r.get("row"),
            "before": before or None}

