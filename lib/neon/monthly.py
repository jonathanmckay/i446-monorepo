"""monthly.py — the 2₦ monthly block of Neon's 1n+ sheet.

Block layout (A61:P90): the header row carries '₹' in D and the month numbers
1..12 in E:P; below it one row per monthly habit: A = due day of month,
B = habit name, D = target 分, E:P = Jan..Dec actuals. The Σ row and the
rolling 3-month efficiency row under it feed /3s and /4gneon-s.

credit_monthly_cell() is the one writer (2026-11-14; /2hci had its own copy,
whose month lookup `rstrip(".0")` turned "10.0" into "1" and so could not
find October). Rows and columns are found by label, never by address.
"""
from __future__ import annotations

SHEET = "1n+"
MONTHLY_HEADER_ROW = 61
MONTHLY_RANGE = "A61:P90"


def _norm(s) -> str:
    return " ".join(str(s).replace("\xa0", " ").split()).lower()


def _month_of(v) -> int | None:
    try:
        f = float(str(v).strip())
    except ValueError:
        return None
    return int(f) if f == int(f) and 1 <= f <= 12 else None


def credit_monthly_cell(habit: str, month: int, points: int | None = None,
                        src: str | None = None) -> dict:
    """Write `points` (default: the row's D target) into the month column of
    the row whose col B is `habit` (case/whitespace-insensitive). Idempotent:
    a non-blank, non-zero cell is left alone.
    Returns {"ok", "row", "col", "value", "skipped"?} or {"ok": False, "error"}."""
    from neon import excel  # inside the call so tests can swap sys.modules["neon"]
    rng = excel.read_range(SHEET, MONTHLY_RANGE)
    if not rng.get("ok"):
        return {"ok": False, "error": rng.get("error", "read_range failed")}
    first = rng.get("row", MONTHLY_HEADER_ROW)
    rows = rng["rows"]
    hdr = rows[MONTHLY_HEADER_ROW - first]
    # months sit right of the D target column; skip A:D so a due-day never matches
    col_idx = next((i for i, v in enumerate(hdr) if i >= 4 and _month_of(v) == int(month)), None)
    want = _norm(habit)
    row_idx = next((i for i, r in enumerate(rows) if len(r) > 1 and _norm(r[1]) == want), None)
    if col_idx is None or row_idx is None:
        return {"ok": False, "error": f"'{habit}' monthly row/col not found (row={row_idx}, col={col_idx})"}
    row = first + row_idx
    col = chr(ord("A") + col_idx)
    target = rows[row_idx][3] if len(rows[row_idx]) > 3 else ""
    pts = points if points is not None else int(float(target or 0))
    current = rows[row_idx][col_idx] if len(rows[row_idx]) > col_idx else ""
    if str(current).strip() not in ("", "0", "0.0"):
        return {"ok": True, "row": row, "col": col, "value": current, "skipped": "already credited"}
    r = excel.batch_write(SHEET, [{"col": col, "value": pts, "mode": "set"}], row=row,
                          src=src or f"{habit} monthly credit", fallback=False)
    return {"ok": bool(r.get("ok")), "row": row, "col": col, "value": pts, "raw": r}
