"""monthly.py — the 2₦ monthly block of Neon's 1n+ sheet.

Block layout (A61:P90): the header row carries '₹' in D and the month numbers
1..12 in E:P; below it one row per monthly habit: A = due day of month,
B = habit name, D = target 分, E:P = Jan..Dec actuals. The Σ row and the
rolling 3-month efficiency row under it feed /3s and /4gneon-s.
C = expected minutes (2026-11-17). Every row has one recurring Todoist card,
label 5^2₦, named as col B (or MONTHLY_ALIASES); tools/2neon audits the cards
and credits the block from Todoist's completion log.

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


LABEL = "5^2₦"                 # every monthly card carries it
FIRST_ROW, LAST_ROW = 62, 87   # the Σ row sums exactly these
# Cards whose names are wired into code (agent_claims SKILL_CLAIMS, 2n1
# CARD_NAME) keep them; every other card is named exactly as col B.
MONTHLY_ALIASES = {
    "2n checksum": "5^2 time audit + checksum for prev. month",
    "5^2 1 n efficiency clearance": "1 ₦ efficiency",
    # Names three cards had before 2026-11-17, kept so their completion
    # history still credits the right row.
    "5^2 f693 org checkin": "5^2 org",
    "cashflow and deal check -> 5^4 g": "dealflow + 现金",
    "special days are special check for next month and plan": "Plan下月特日",
    "streamline bag": "streamline bags",
}
# Aliases that are a card's CURRENT name: tools/2neon must not rename these.
WIRED_CARD_NAMES = ("2n checksum", "5^2 1 n efficiency clearance")

import re as _re

# Only numeric annotations: "(15)", "[60]", "{60}", "[0G]". Row names carry
# their own parentheses ("5^2 s (上月的)", "Special Event (s)").
_ANNOT = _re.compile(r"[\(\[\{]\s*\d+[A-Za-z]?\s*[\)\]\}]")
_STAMP = _re.compile(r"^(\d{1,2})\.(\d{1,2})$")


def card_key(content: str) -> tuple[str, int | None]:
    """(normalised name, month of a deferred copy's M.D stamp or None) for a
    card's content: (N) [N] {N} and @labels dropped. defer-fast puts the stamp
    before the first bracket, so it can sit mid-name: '5^2 s 10.3 (上月的)'."""
    month, words = None, []
    for w in _ANNOT.sub(" ", str(content)).split():
        m = _STAMP.match(w)
        if w.startswith("@"):
            continue
        if m and month is None and 1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 31:
            month = int(m.group(1))
            continue
        words.append(w)
    return _norm(" ".join(words)), month


def row_name_for(content: str, names) -> tuple[str | None, int | None]:
    """The block row (col B, as written) a card's content belongs to, plus the
    deferred-copy month. Strict equality on the normalised name, or an alias."""
    key, month = card_key(content)
    key = _norm(MONTHLY_ALIASES.get(key, key))
    for n in names:
        if _norm(n) == key:
            return n, month
    return None, month


def _num(v):
    try:
        f = float(str(v).strip())
    except ValueError:
        return None
    return int(f) if f == int(f) else f


def block_rows() -> list[dict]:
    """The block's definitions and actuals: [{"row", "day", "name", "minutes",
    "points", "actuals": {month: value}}], one per named row. Raises on a
    failed read so callers never act on an empty block."""
    from neon import excel
    rng = excel.read_range(SHEET, MONTHLY_RANGE)
    if not rng.get("ok"):
        raise RuntimeError(rng.get("error", "read_range failed"))
    first = rng.get("row", MONTHLY_HEADER_ROW)
    rows = rng["rows"]
    hdr = rows[MONTHLY_HEADER_ROW - first]
    months = {i: _month_of(v) for i, v in enumerate(hdr) if i >= 4 and _month_of(v)}
    out = []
    for i, r in enumerate(rows):
        n = first + i
        r = list(r) + [""] * (16 - len(r))
        if not (FIRST_ROW <= n <= LAST_ROW) or not str(r[1]).strip():
            continue
        out.append({"row": n, "day": _num(r[0]), "name": str(r[1]).replace("\xa0", " ").strip(),
                    "minutes": _num(r[2]), "points": _num(r[3]),
                    "actuals": {m: _num(r[c]) for c, m in months.items()}})
    return out


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
