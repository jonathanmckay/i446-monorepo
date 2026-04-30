#!/usr/bin/env python3
"""
excel-http — tiny localhost daemon on ix that fronts AppleScript writes to Neon.

Cuts the per-write cost from ~2-3s (ssh+osascript cold spawn each time) to
~200-400ms (skip SSH; one osascript spawn per request, but the interpreter
warms up after a few hits).

Endpoints (POST JSON bodies):

  POST /append   {sheet, col, date, value}        # value like "+10" or "+'1n+'!S20"
  POST /write    {sheet, col, date|row, value}    # set cell to value (literal or =formula)
  POST /read     {sheet, col, date|row}           # → {value, formula}
  POST /lookup   {sheet, date}                    # → {row}
  GET  /health                                    # → {ok: true, version}

Sheet date-column resolution is hardcoded to match neon-cols.json:
  0分 → B,  0n → C,  1n+ → B,  hcbi → B

Bind to 127.0.0.1:9876 by default. Skills SSH to ix and curl localhost.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

VERSION = "1.0.0"
ADDR = ("127.0.0.1", 9876)
TIMEOUT = 15  # osascript hard timeout

DATE_COL = {"0分": "B", "0n": "C", "1n+": "B", "hcbi": "B"}


def osascript(script: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=TIMEOUT,
    )
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def lookup_row(sheet: str, date_str: str) -> int | None:
    """Find the row in `sheet` where the date column equals `date_str` (M/D)."""
    dc = DATE_COL.get(sheet)
    if not dc:
        return None
    script = f'''
tell application "Microsoft Excel"
    set theSheet to sheet "{sheet}" of active workbook
    repeat with i from 2 to 800
        if (string value of cell ("{dc}" & i) of theSheet) = "{date_str}" then
            return i
        end if
    end repeat
    return 0
end tell
'''
    rc, out, err = osascript(script)
    if rc != 0:
        return None
    try:
        n = int(out)
        return n if n > 0 else None
    except ValueError:
        return None


def safe_str(s: str) -> str:
    """Escape backslashes and double quotes for embedding inside an AS string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def cell_addr(req: dict) -> tuple[str, int] | None:
    """Resolve the (col, row) target from the request body."""
    sheet = req.get("sheet")
    col = req.get("col")
    if not sheet or not col:
        return None
    if "row" in req and req["row"]:
        return col, int(req["row"])
    if "date" in req and req["date"]:
        r = lookup_row(sheet, req["date"])
        if r is None:
            return None
        return col, r
    return None


def do_append(req: dict) -> dict:
    addr = cell_addr(req)
    if not addr:
        return {"ok": False, "error": "date_not_found_or_missing_target"}
    col, row = addr
    sheet = req["sheet"]
    val = str(req.get("value", ""))
    val_esc = safe_str(val)
    script = f'''
tell application "Microsoft Excel"
    set theSheet to sheet "{sheet}" of active workbook
    set theCell to cell ("{col}{row}") of theSheet
    set oldFormula to formula of theCell
    if oldFormula = "" or oldFormula = "0" then
        set formula of theCell to "={val_esc.lstrip("+")}"
    else
        set formula of theCell to oldFormula & "{val_esc}"
    end if
    return ((value of theCell) as string) & "|" & (formula of theCell)
end tell
'''
    rc, out, err = osascript(script)
    if rc != 0:
        return {"ok": False, "error": err}
    value, formula = (out.split("|", 1) + [""])[:2]
    return {"ok": True, "row": row, "col": col, "value": value, "formula": formula}


def do_write(req: dict) -> dict:
    addr = cell_addr(req)
    if not addr:
        return {"ok": False, "error": "date_not_found_or_missing_target"}
    col, row = addr
    sheet = req["sheet"]
    val = str(req.get("value", ""))
    val_esc = safe_str(val)
    is_formula = val.startswith("=")
    setter = "formula" if is_formula else "value"
    script = f'''
tell application "Microsoft Excel"
    set theCell to cell ("{col}{row}") of sheet "{sheet}" of active workbook
    set {setter} of theCell to "{val_esc}"
    return ((value of theCell) as string) & "|" & (formula of theCell)
end tell
'''
    rc, out, err = osascript(script)
    if rc != 0:
        return {"ok": False, "error": err}
    value, formula = (out.split("|", 1) + [""])[:2]
    return {"ok": True, "row": row, "col": col, "value": value, "formula": formula}


def do_read(req: dict) -> dict:
    addr = cell_addr(req)
    if not addr:
        return {"ok": False, "error": "date_not_found_or_missing_target"}
    col, row = addr
    sheet = req["sheet"]
    script = f'''
tell application "Microsoft Excel"
    set theCell to cell ("{col}{row}") of sheet "{sheet}" of active workbook
    return ((value of theCell) as string) & "|" & (formula of theCell)
end tell
'''
    rc, out, err = osascript(script)
    if rc != 0:
        return {"ok": False, "error": err}
    value, formula = (out.split("|", 1) + [""])[:2]
    return {"ok": True, "row": row, "col": col, "value": value, "formula": formula}


def do_lookup(req: dict) -> dict:
    sheet = req.get("sheet")
    date_str = req.get("date")
    if not sheet or not date_str:
        return {"ok": False, "error": "missing_sheet_or_date"}
    r = lookup_row(sheet, date_str)
    return {"ok": True, "row": r} if r else {"ok": False, "error": "date_not_found"}


ROUTES = {
    "/append": do_append,
    "/write":  do_write,
    "/read":   do_read,
    "/lookup": do_lookup,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self._send(200, {"ok": True, "version": VERSION})
        return self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self):  # noqa: N802
        handler = ROUTES.get(self.path)
        if not handler:
            return self._send(404, {"ok": False, "error": "not_found"})
        n = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(n)) if n else {}
        except json.JSONDecodeError:
            return self._send(400, {"ok": False, "error": "bad_json"})
        try:
            result = handler(body)
        except subprocess.TimeoutExpired:
            return self._send(504, {"ok": False, "error": "osascript_timeout"})
        except Exception as e:
            return self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        return self._send(200 if result.get("ok") else 400, result)


def main():
    srv = HTTPServer(ADDR, Handler)
    print(f"excel-http v{VERSION} listening on {ADDR[0]}:{ADDR[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.server_close()


if __name__ == "__main__":
    main()
