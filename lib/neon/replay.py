"""Replay model: what the excel-http setters leave in a cell for a request.

One implementation for the offline dry-run (tools/neon-ledger/replay_check.py)
and for the daemon's write queue (services/excel-http: deciding whether a
request that timed out has already landed). Mirrors server.py do_write /
_as_setter / _append_setters; test_replay.py pins it to journaled reality.
"""
from __future__ import annotations

import re

_NUM = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
_POINT_ZERO = re.compile(r"(?<![\d.])(\d+)\.0+(?![\d])")   # Excel stores "+15.0" as "+15"


def _s(v) -> str:
    return "" if v is None else str(v)


def num(s):
    s = _s(s).strip()
    return float(s) if _NUM.match(s) else None


def same(a, b) -> bool:
    """Cell contents equal: exact after trimming, or numerically ("1" ≡ "1.0"),
    or as formulas that differ only by a trailing ".0" on a literal."""
    a, b = _s(a).strip(), _s(b).strip()
    if a == b:
        return True
    if a.startswith("=") and b.startswith("=") and _POINT_ZERO.sub(r"\1", a) == _POINT_ZERO.sub(r"\1", b):
        return True
    x, y = num(a), num(b)
    return x is not None and y is not None and abs(x - y) < 1e-9


def apply(before, kind: str, value) -> str:
    """Content of a cell holding formula `before` after a request (kind, value).
    kind "write" replaces the cell; "append" follows the daemon's append
    normalization. Mode "add" is a separate question: see is_add()."""
    before = _s(before)
    val = _s(value)
    if kind == "write":
        return val
    if kind != "append":
        raise ValueError(kind)
    numeric = val.lstrip().startswith(("+", "=", "-"))
    if before in ("", "0"):
        return "=" + val.lstrip("+") if numeric else val.lstrip(", ")
    if numeric and not before.startswith("="):
        return "=" + before + val
    return before + val


def is_add(before, value, after) -> bool:
    """A numeric write whose result is old + N rather than N: mode "add"."""
    b, v, a = num(before), num(value), num(after)
    if v is None or a is None or b in (None, 0.0):
        return False
    return abs((b + v) - a) < 1e-9 and abs(v - a) > 1e-9


def added(before, value, after) -> bool:
    """True when `after` is `before` + N for a mode-add request, counting a
    blank or 0 cell as 0 (the daemon's add sets N outright there)."""
    v, a = num(value), num(after)
    if v is None or a is None:
        return False
    b = num(before) or 0.0
    return abs((b + v) - a) < 1e-9
