"""Client for the excel-http daemon on ix.

Tries the daemon first (via `ssh ix curl localhost:9876`); falls back to
the legacy `ssh ix osascript ...` path if the daemon isn't reachable.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from typing import Any

DAEMON_HOST = "ix"
DAEMON_PORT = 9876
DAEMON_TIMEOUT = 5


def _curl(path: str, body: dict | None = None, *, method: str = "POST") -> dict | None:
    """Invoke the daemon over SSH. Returns parsed JSON, or None on failure."""
    if body is None:
        cmd = f"curl -sS -m 10 http://localhost:{DAEMON_PORT}{path}"
    else:
        payload = json.dumps(body, ensure_ascii=False)
        cmd = (
            f"curl -sS -m 10 -X {method} -H 'Content-Type: application/json' "
            f"-d {shlex.quote(payload)} http://localhost:{DAEMON_PORT}{path}"
        )
    try:
        r = subprocess.run(
            ["ssh", DAEMON_HOST, cmd],
            capture_output=True, text=True, timeout=DAEMON_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def health() -> bool:
    out = _curl("/health", method="GET")
    return bool(out and out.get("ok"))


# ── Public API ────────────────────────────────────────────────────────────────

def append(sheet: str, col: str, *, date: str | None = None,
           row: int | None = None, value: str) -> dict[str, Any]:
    """Append `value` (e.g. '+10', "+'1n+'!S20") to a cell formula.

    Pass either `date` (M/D) for date-row lookup, or `row` for direct addressing.
    """
    body = {"sheet": sheet, "col": col, "value": value}
    if date is not None:
        body["date"] = date
    if row is not None:
        body["row"] = row
    out = _curl("/append", body)
    if out:
        return out
    return _ssh_fallback("append", sheet, col, date, row, value)


def write(sheet: str, col: str, *, date: str | None = None,
          row: int | None = None, value: str) -> dict[str, Any]:
    body = {"sheet": sheet, "col": col, "value": value}
    if date is not None:
        body["date"] = date
    if row is not None:
        body["row"] = row
    out = _curl("/write", body)
    if out:
        return out
    return _ssh_fallback("write", sheet, col, date, row, value)


def read(sheet: str, col: str, *, date: str | None = None,
         row: int | None = None) -> dict[str, Any]:
    body = {"sheet": sheet, "col": col}
    if date is not None:
        body["date"] = date
    if row is not None:
        body["row"] = row
    out = _curl("/read", body)
    if out:
        return out
    return _ssh_fallback("read", sheet, col, date, row, None)


def lookup_row(sheet: str, date_str: str) -> int | None:
    out = _curl("/lookup", {"sheet": sheet, "date": date_str})
    if out and out.get("ok"):
        return out.get("row")
    return None


# ── ssh+osascript fallback ────────────────────────────────────────────────────

_DATE_COL = {"0分": "B", "0n": "C", "1n+": "B", "hcbi": "B"}


def _ssh_fallback(op: str, sheet: str, col: str,
                  date: str | None, row: int | None,
                  value: str | None) -> dict[str, Any]:
    """If the daemon is unreachable, fall back to one-shot ssh+osascript."""
    if row is None and date is not None:
        dc = _DATE_COL.get(sheet, "B")
        lookup_script = (
            f'tell application "Microsoft Excel" to '
            f'(repeat with i from 2 to 800\n'
            f'  if (string value of cell ("{dc}" & i) of sheet "{sheet}" of active workbook) = "{date}" then return i\n'
            f'end repeat\nreturn 0)'
        )
        r = subprocess.run(
            ["ssh", DAEMON_HOST, "osascript", "-e", lookup_script],
            capture_output=True, text=True, timeout=15,
        )
        try:
            row = int(r.stdout.strip())
        except ValueError:
            row = 0
        if not row:
            return {"ok": False, "error": "date_not_found_fallback", "fallback": True}

    if op == "read":
        script = (
            f'tell application "Microsoft Excel" to '
            f'return ((value of cell "{col}{row}" of sheet "{sheet}" of active workbook) as string) '
            f'& "|" & (formula of cell "{col}{row}" of sheet "{sheet}" of active workbook)'
        )
    elif op == "append":
        v = (value or "").replace("\\", "\\\\").replace('"', '\\"')
        script = (
            f'tell application "Microsoft Excel"\n'
            f'  set theCell to cell "{col}{row}" of sheet "{sheet}" of active workbook\n'
            f'  set f to formula of theCell\n'
            f'  if f = "" or f = "0" then\n'
            f'    set formula of theCell to "={v.lstrip("+")}"\n'
            f'  else\n'
            f'    set formula of theCell to f & "{v}"\n'
            f'  end if\n'
            f'  return ((value of theCell) as string) & "|" & (formula of theCell)\n'
            f'end tell'
        )
    else:  # write
        v = (value or "").replace("\\", "\\\\").replace('"', '\\"')
        setter = "formula" if (value or "").startswith("=") else "value"
        script = (
            f'tell application "Microsoft Excel"\n'
            f'  set theCell to cell "{col}{row}" of sheet "{sheet}" of active workbook\n'
            f'  set {setter} of theCell to "{v}"\n'
            f'  return ((value of theCell) as string) & "|" & (formula of theCell)\n'
            f'end tell'
        )
    r = subprocess.run(
        ["ssh", DAEMON_HOST, "osascript", "-e", script],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        return {"ok": False, "error": r.stderr.strip(), "fallback": True}
    parts = r.stdout.strip().split("|", 1)
    val, formula = (parts + [""])[:2]
    return {"ok": True, "row": row, "col": col, "value": val,
            "formula": formula, "fallback": True}
