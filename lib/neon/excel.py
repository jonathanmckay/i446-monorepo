"""Client for the excel-http daemon on ix.

Tries the daemon first (via `ssh ix curl localhost:9876`); falls back to
the legacy `ssh ix osascript ...` path if the daemon isn't reachable.
"""

from __future__ import annotations

import datetime
import json
import shlex
import socket
import subprocess
from typing import Any

DAEMON_HOST = "ix"
DAEMON_PORT = 9876
# The daemon binds 127.0.0.1 on ix; when this client is already running ON ix
# (build-order-daemon, janus-mobile, dtd-web) an ssh hop to ourselves is pure
# waste and can wedge on a stale MagicSock — curl localhost directly instead.
IS_IX = "mac-mini" in socket.gethostname().lower()
LEDGER_DIR = "/Users/mckay/vault/g245/neon-ledger"
# Covers a fresh ssh handshake on a congested tailnet path (~10s observed
# 2026-07-20 at ~630ms RTT) plus curl's own -m 20. At 5s every call fell
# through to the (then-broken) osascript fallback whenever no connection
# was already warm.
#
# curl's own -m must stay comfortably BELOW both python timeouts below, or
# python kills the subprocess before curl's timeout ever fires cleanly.
# 2026-08-09: curl -m 10 was too tight for genuine (non-network) Excel
# write latency — a legitimate daemon append took ~12s, curl gave up at
# 10s, the caller fell back to a non-idempotent ssh+osascript write, and
# the original daemon request landed a few seconds later anyway. Result:
# the append happened twice (100pts instead of 50 in 0分 col Q that
# morning). Raised curl's -m to 20 and both python timeouts to match, so
# a slow-but-successful Excel write no longer races the fallback.
CURL_TIMEOUT = 20
DAEMON_TIMEOUT = 25
WORKBOOK = "Neon分v12.2.xlsx"


def _curl(path: str, body: dict | None = None, *, method: str = "POST") -> dict | None:
    """Invoke the daemon (over SSH, or locally when already on ix). Returns parsed JSON, or None on failure."""
    if body is None:
        cmd = f"curl -sS -m {CURL_TIMEOUT} http://localhost:{DAEMON_PORT}{path}"
    else:
        payload = json.dumps(body, ensure_ascii=False)
        cmd = (
            f"curl -sS -m {CURL_TIMEOUT} -X {method} -H 'Content-Type: application/json' "
            f"-d {shlex.quote(payload)} http://localhost:{DAEMON_PORT}{path}"
        )
    argv = ["sh", "-c", cmd] if IS_IX else ["ssh", DAEMON_HOST, cmd]
    try:
        r = subprocess.run(
            argv,
            capture_output=True, text=True,
            timeout=DAEMON_TIMEOUT,
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
           row: int | None = None, value: str, src: str | None = None) -> dict[str, Any]:
    """Append `value` (e.g. '+10', "+'1n+'!S20") to a cell formula.

    Pass either `date` (M/D) for date-row lookup, or `row` for direct addressing.
    `src` labels the write in the neon ledger (who/what earned it).
    """
    body = {"sheet": sheet, "col": col, "value": value}
    if date is not None:
        body["date"] = date
    if row is not None:
        body["row"] = row
    if src:
        body["src"] = src
    out = _curl("/append", body)
    if out:
        return out
    return _ssh_fallback("append", sheet, col, date, row, value, src=src)


def write(sheet: str, col: str, *, date: str | None = None,
          row: int | None = None, value: str, src: str | None = None) -> dict[str, Any]:
    body = {"sheet": sheet, "col": col, "value": value}
    if date is not None:
        body["date"] = date
    if row is not None:
        body["row"] = row
    if src:
        body["src"] = src
    out = _curl("/write", body)
    if out:
        return out
    return _ssh_fallback("write", sheet, col, date, row, value, src=src)


def batch_append(sheet: str, appends: list, *, date: str | None = None,
                 row: int | None = None, src: str | None = None) -> dict[str, Any]:
    """N formula-appends to one sheet/date in a single daemon round-trip.
    `appends` = [(col, value), ...] or [{"col":…, "value":…, "src":…}, …].
    Falls back to per-cell ssh appends when the daemon is down."""
    items = [a if isinstance(a, dict) else {"col": a[0], "value": a[1]} for a in appends]
    body = {"sheet": sheet, "appends": items}
    if date is not None:
        body["date"] = date
    if row is not None:
        body["row"] = row
    if src:
        body["src"] = src
    out = _curl("/batch", body)
    if out:
        return out
    results = [_ssh_fallback("append", sheet, it["col"], date, row, it["value"],
                             src=it.get("src") or src) for it in items]
    return {"ok": all(r.get("ok") for r in results), "results": results, "fallback": True}


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


def _journal_fallback(op: str, sheet: str, col: str, row: int | None,
                      date: str | None, value: str | None,
                      after_formula: str, src: str | None) -> None:
    """Best-effort ledger entry for a write that bypassed the daemon.

    The daemon couldn't journal it (it was down), so append the JSONL line
    ourselves on ix. before_formula is unknown (null) — the audit treats
    fallback entries as warn-only chain links, not breaks.
    """
    now = datetime.datetime.now()
    entry = {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "kind": op, "sheet": sheet, "col": col, "row": row, "date": date,
        "value": value, "before": None, "after": after_formula,
        "src": src, "fallback": True, "host": socket.gethostname(),
    }
    line = json.dumps(entry, ensure_ascii=False)
    path = f"{LEDGER_DIR}/{now.strftime('%Y-%m')}.jsonl"
    cmd = f"mkdir -p {shlex.quote(LEDGER_DIR)} && printf '%s\\n' {shlex.quote(line)} >> {shlex.quote(path)}"
    argv = ["sh", "-c", cmd] if IS_IX else ["ssh", DAEMON_HOST, cmd]
    try:
        subprocess.run(argv, capture_output=True, text=True, timeout=20)
    except Exception:
        pass  # journaling must never fail the write it describes


def _ssh_fallback(op: str, sheet: str, col: str,
                  date: str | None, row: int | None,
                  value: str | None, src: str | None = None) -> dict[str, Any]:
    """If the daemon is unreachable, fall back to one-shot ssh+osascript."""
    if row is None and date is not None:
        dc = _DATE_COL.get(sheet, "B")
        # Match an M/D text cell OR a real Excel date cell (0n col C) by month/day
        # — see lookup_row() in services/excel-http/server.py for the rationale.
        lookup_script = (
            f'tell application "Microsoft Excel"\n'
            f'  set ws to sheet "{sheet}" of workbook "{WORKBOOK}"\n'
            f'  repeat with i from 2 to 800\n'
            f'    set cv to value of cell ("{dc}" & i) of ws\n'
            f'    if cv is not missing value then\n'
            f'      if ((class of cv) as text) is "date" then\n'
            f'        set md to (((month of cv) as integer) as text) & "/" & ((day of cv) as text)\n'
            f'        if md = "{date}" then return i\n'
            f'      else\n'
            f'        if (cv as text) = "{date}" then return i\n'
            f'      end if\n'
            f'    end if\n'
            f'  end repeat\n'
            f'  return 0\n'
            f'end tell'
        )
        # ssh joins argv with spaces for the remote shell — the script MUST be
        # shell-quoted or the remote zsh parses its parens/quotes and dies
        # ("parse error near ')'", found 2026-07-20: the fallback had never
        # actually worked).
        r = subprocess.run(
            ["ssh", DAEMON_HOST, f"osascript -e {shlex.quote(lookup_script)}"],
            capture_output=True, text=True, timeout=45,
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
            f'return ((value of cell "{col}{row}" of sheet "{sheet}" of workbook "{WORKBOOK}") as string) '
            f'& "|" & (formula of cell "{col}{row}" of sheet "{sheet}" of workbook "{WORKBOOK}")'
        )
    elif op == "append":
        v = (value or "").replace("\\", "\\\\").replace('"', '\\"')
        # "-" is a formula term like "+" (e.g. the 0t sleep dock appends "-7.5");
        # without it a negative append took the string branch and produced
        # non-computing text on bare-number cells (same class as the 2026-07-14
        # "+2" regression below).
        is_numeric = (value or "").lstrip().startswith(("+", "=", "-"))
        if is_numeric:
            empty_set = f'set formula of theCell to "={v.lstrip("+")}"\n'
            # The existing cell may hold a bare number with no leading "="
            # (e.g. a plain value-set "2", not a formula). Concatenating
            # "+2" onto that directly produces the TEXT "2+2" instead of a
            # formula, so it never computes (observed live 2026-07-14: a
            # Daily Dozen count cell silently stopped summing). Normalize
            # to a formula before appending.
            nonempty_set = (
                f'if f does not start with "=" then\n'
                f'      set formula of theCell to "=" & f & "{v}"\n'
                f'    else\n'
                f'      set formula of theCell to f & "{v}"\n'
                f'    end if\n'
            )
        else:
            # String value: strip leading ", " for empty cells, set as value not formula
            clean = v.lstrip(", ")
            empty_set = f'set value of theCell to "{clean}"\n'
            nonempty_set = f'set formula of theCell to f & "{v}"\n'
        script = (
            f'tell application "Microsoft Excel"\n'
            f'  set theCell to cell "{col}{row}" of sheet "{sheet}" of workbook "{WORKBOOK}"\n'
            f'  set f to formula of theCell\n'
            f'  if f = "" or f = "0" then\n'
            f'    {empty_set}'
            f'  else\n'
            f'    {nonempty_set}'
            f'  end if\n'
            f'  return ((value of theCell) as string) & "|" & (formula of theCell)\n'
            f'end tell'
        )
    else:  # write
        v = (value or "").replace("\\", "\\\\").replace('"', '\\"')
        setter = "formula" if (value or "").startswith("=") else "value"
        script = (
            f'tell application "Microsoft Excel"\n'
            f'  set theCell to cell "{col}{row}" of sheet "{sheet}" of workbook "{WORKBOOK}"\n'
            f'  set {setter} of theCell to "{v}"\n'
            f'  return ((value of theCell) as string) & "|" & (formula of theCell)\n'
            f'end tell'
        )
    osa = f"osascript -e {shlex.quote(script)}"
    r = subprocess.run(
        ["sh", "-c", osa] if IS_IX else ["ssh", DAEMON_HOST, osa],
        capture_output=True, text=True, timeout=45,
    )
    if r.returncode != 0:
        return {"ok": False, "error": r.stderr.strip(), "fallback": True}
    parts = r.stdout.strip().split("|", 1)
    val, formula = (parts + [""])[:2]
    if op in ("append", "write"):
        _journal_fallback(op, sheet, col, row, date, value, formula, src)
    return {"ok": True, "row": row, "col": col, "value": val,
            "formula": formula, "fallback": True}
