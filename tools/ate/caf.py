#!/usr/bin/env python3
"""caf.py — /caf: write caffeine points to hcbi's `caf` column (W) on the day
the text names (2026-11-01).

    caf.py "<points> [when]"        write, print one line
    caf.py --parse "<text>"         print {"points", "date"} and write nothing

Points: one signed number anywhere in the text (-60, 120, +50); an M.D
token is the date when another number is present, in either order. Everything
else is the date phrase; with none it is today (daytime.today(), travel-aware):

    yesterday · tomorrow · N days ago · <weekday> / last <weekday> (the most
    recent one, today if it is that weekday) · M.D / M/D (this year, last
    year if that day has not happened yet) · YYYY-MM-DD

The cell is SET (mode "set"), not accumulated: a second /caf on the same day
replaces the first, and the confirmation shows what it replaced. The column
letter comes from config/neon-cols.json (hcbi → caf), never hardcoded.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

LIB = Path.home() / "i446-monorepo" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
            "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
            "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
_NUM = re.compile(r"(?<![\d./-])[+-]?\d+(?:\.\d+)?(?![\d./-])")
_MD = re.compile(r"^(\d{1,2})[./](\d{1,2})$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse(text: str, today: dt.date | None = None) -> dict:
    """{"points": float, "date": date} from free text. Raises ValueError when
    there is no number or the date phrase is not understood."""
    today = today or _today()
    s = text.strip()
    m = _points_match(s)
    if not m:
        raise ValueError("no points in text")
    points = float(m.group(0))
    rest = (s[:m.start()] + " " + s[m.end():]).lower()
    rest = re.sub(r"\b(pts?|points?|caf|caffeine|on|for|the)\b", " ", rest)
    toks = [t for t in re.split(r"\s+", rest.strip()) if t]
    return {"points": points, "date": when(toks, today)}


def _looks_like_md(tok: str) -> bool:
    m = _MD.fullmatch(tok)
    return bool(m) and 1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 31


def _points_match(s: str):
    """The number that is the points. An unsigned M.D token ("10.30") is a
    date whenever another number is present, so "10.30 -60" (date first) parses
    like "-60 10.30" (2026-11-02: it read 10.30 as the points). A lone number
    is always the points."""
    ms = list(_NUM.finditer(s))
    if len(ms) > 1:
        for m in ms:
            if not _looks_like_md(m.group(0)):
                return m
    return ms[0] if ms else None


def when(toks: list[str], today: dt.date) -> dt.date:
    if not toks or toks == ["today"]:
        return today
    phrase = " ".join(toks)
    if phrase == "yesterday":
        return today - dt.timedelta(days=1)
    if phrase == "tomorrow":
        return today + dt.timedelta(days=1)
    m = re.fullmatch(r"(\d+) days? ago", phrase)
    if m:
        return today - dt.timedelta(days=int(m.group(1)))
    m = re.fullmatch(r"(?:last )?([a-z]+)", phrase)
    if m and m.group(1) in WEEKDAYS:
        back = (today.weekday() - WEEKDAYS[m.group(1)]) % 7
        if phrase.startswith("last ") and back == 0:
            back = 7
        return today - dt.timedelta(days=back)
    m = _ISO.fullmatch(phrase)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _MD.fullmatch(phrase)
    if m:
        d = dt.date(today.year, int(m.group(1)), int(m.group(2)))
        return d if d <= today + dt.timedelta(days=1) else d.replace(year=today.year - 1)
    raise ValueError(f"don't understand the date {phrase!r}")


def _today() -> dt.date:
    import daytime
    return daytime.today()


def write(text: str, today: dt.date | None = None, excel=None, cols=None) -> dict:
    p = parse(text, today)
    if excel is None or cols is None:
        from neon import excel as _excel, cols as _cols
        excel, cols = excel or _excel, cols or _cols
    col = cols.col("hcbi", "caf")
    d = p["date"]
    md = f"{d.month}/{d.day}"
    v = int(p["points"]) if p["points"] == int(p["points"]) else p["points"]
    r = excel.batch_write("hcbi", [{"col": col, "value": v, "mode": "set"}], date=md,
                          src=f"caf {v} {d.isoformat()}")
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error") or r, "date": d.isoformat()}
    results = r.get("results") or []          # daemon: {"ok", "row", "results": [{before_value, ...}]}
    cell = results[0] if results else {}
    before = (cell.get("before_value") or "").strip()
    return {"ok": True, "points": v, "date": d.isoformat(), "col": col, "row": r.get("row"),
            "before": before or None}


def fmt(out: dict) -> str:
    if not out.get("ok"):
        return f"ERROR: {out.get('error')} ({out.get('date', '?')})"
    was = f", was {out['before']}" if out.get("before") is not None else ""
    return f"caf → {out['points']} ({out['date']}, hcbi {out['col']}{out['row']}{was})"


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip()); return 2
    if argv[0] == "--parse":
        try:
            p = parse(" ".join(argv[1:]))
        except ValueError as e:
            print(json.dumps({"ok": False, "error": str(e)})); return 1
        print(json.dumps({"ok": True, "points": p["points"], "date": p["date"].isoformat()})); return 0
    try:
        out = write(" ".join(argv))
    except ValueError as e:
        print(f"ERROR: {e}"); return 1
    print(fmt(out))
    return 0 if out.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
