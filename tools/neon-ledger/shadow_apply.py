#!/usr/bin/env python3
"""shadow_apply — what the ledger-first block job WOULD write to Excel, vs
what Excel holds right now. Neon off Excel step 4, shadow phase (2026-11-11).

Cron on Ix at HH:50 for every fire hour (03:50 … 21:50, config/ix.crontab):
ten minutes before build-order-daemon's lock-and-mark at HH:00, and clear of
its 20 s Excel health probe, which kills a slow Excel (rubber-duck: a :59 read
could be the thing that makes the probe time out).

Per run: one live `read_range` of today's 0分 row D..Y, one strict
`rules.derive_day`, one JSONL line per point column (and Σ) to
~/.cache/neon-rules/shadow-apply/<date>.jsonl, plus a rolling
shadow-summary.json (per day: boundaries, clean boundaries, columns that
ever disagreed) that the 11-14 cutover check reads. NO Excel writes.

A boundary is "clean" when every point column with no unknown inputs agrees
with Excel within rules.TOL. Columns with unknown inputs are reported, not
judged (same rule as the nightly diff).

  shadow_apply.py [--date YYYY-MM-DD] [--hour HH] [--print]
  shadow_apply.py --summary            # print shadow-summary.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from neon import excel, ledger, rules  # noqa: E402

OUT_DIR = Path(os.path.expanduser("~/.cache/neon-rules/shadow-apply"))
SUMMARY = OUT_DIR / "shadow-summary.json"
RANGE_COLS = [chr(c) for c in range(ord("D"), ord("Y") + 1)]   # D..Y, one read


def _num(s) -> float | None:
    try:
        v = float(str(s).strip())
    except (TypeError, ValueError):
        return None
    return v


def compare(derived: dict, excel_row: dict, tol: float = rules.TOL) -> list:
    """Pure: {col: {derived, unknown}} × {col: excel value|None} → one record
    per column. judged=False when inputs are unknown or Excel is blank."""
    out = []
    for col in rules.POINT_COLS + ["D"]:
        d = derived.get(col) or {}
        dv, unk = d.get("derived"), list(d.get("unknown") or [])
        xv = excel_row.get(col)
        judged = dv is not None and xv is not None and not unk
        delta = None if (dv is None or xv is None) else round(dv - xv, 3)
        out.append({"col": col, "field": rules._header("0分", col) or col,
                    "derived": None if dv is None else round(dv, 3), "excel": xv,
                    "delta": delta, "unknown": unk, "judged": judged,
                    "agree": (judged and abs(delta) <= tol) if judged else None,
                    "would_write": None if dv is None else round(dv, 3)})
    return out


def read_excel_row(date_iso: str) -> tuple[int | None, dict]:
    d = _dt.date.fromisoformat(date_iso)
    row = excel.lookup_row("0分", "%d/%d" % (d.month, d.day))
    if not row:
        return None, {}
    r = excel.read_range("0分", f"D{row}:Y{row}")
    if not r.get("ok") or not r.get("rows"):
        return row, {}
    vals = r["rows"][0]
    return row, {col: _num(vals[i]) if i < len(vals) else None for i, col in enumerate(RANGE_COLS)}


def run(date_iso: str, hour: int, print_out: bool = False) -> dict:
    ts = _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    ledger.catch_up()
    c = ledger.connect()
    try:
        day = rules.derive_day(date_iso, c, strict=True)
    finally:
        c.close()
    row, xrow = read_excel_row(date_iso)
    recs = compare(day["cols"], xrow)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / f"{date_iso}.jsonl").open("a") as f:
        for r in recs:
            f.write(json.dumps({"ts": ts, "hour": hour, "row": row, **r}, ensure_ascii=False) + "\n")
    judged = [r for r in recs if r["judged"]]
    clean = bool(xrow) and all(r["agree"] for r in judged)
    boundary = {"ts": ts, "hour": hour, "excel_read": bool(xrow), "clean": clean,
                "disagree": sorted(r["col"] for r in judged if not r["agree"]),
                "unjudged": sorted(r["col"] for r in recs if not r["judged"])}
    _update_summary(date_iso, boundary)
    if print_out:
        for r in recs:
            mark = "✅" if r["agree"] else ("❌" if r["judged"] else "…")
            print(f"{mark} {r['col']} {r['field']:<5} ledger {r['derived']!s:>9} excel {r['excel']!s:>9}"
                  f"{'  unknown: ' + ', '.join(r['unknown']) if r['unknown'] else ''}")
        print(("clean" if clean else "DIRTY") + f" boundary {hour:02d}:xx row {row}")
    return boundary


def _update_summary(date_iso: str, boundary: dict) -> None:
    s = {"days": {}}
    if SUMMARY.exists():
        try:
            s = json.loads(SUMMARY.read_text())
        except ValueError:
            pass
    d = s["days"].setdefault(date_iso, {"boundaries": 0, "clean": 0, "excel_unreachable": 0,
                                        "ever_disagreed": [], "last": None})
    d["boundaries"] += 1
    d["clean"] += 1 if boundary["clean"] else 0
    d["excel_unreachable"] += 0 if boundary["excel_read"] else 1
    d["ever_disagreed"] = sorted(set(d["ever_disagreed"]) | set(boundary["disagree"]))
    d["last"] = boundary
    s["generated"] = boundary["ts"]
    s["total_boundaries"] = sum(x["boundaries"] for x in s["days"].values())
    s["total_clean"] = sum(x["clean"] for x in s["days"].values())
    SUMMARY.write_text(json.dumps(s, ensure_ascii=False, indent=1) + "\n")


def main() -> int:
    p = argparse.ArgumentParser(prog="shadow_apply")
    p.add_argument("--date")
    p.add_argument("--hour", type=int)
    p.add_argument("--print", action="store_true")
    p.add_argument("--summary", action="store_true")
    a = p.parse_args()
    if a.summary:
        print(SUMMARY.read_text() if SUMMARY.exists() else "{}")
        return 0
    now = _dt.datetime.now()
    date_iso = a.date or now.date().isoformat()
    hour = a.hour if a.hour is not None else (now.hour + 1 if now.minute >= 30 else now.hour)
    b = run(date_iso, hour, print_out=a.print)
    return 0 if b["excel_read"] else 2


if __name__ == "__main__":
    sys.exit(main())
