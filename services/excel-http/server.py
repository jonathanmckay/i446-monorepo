#!/usr/bin/env python3
"""
excel-http — tiny localhost daemon on ix that fronts AppleScript writes to Neon.

Cuts the per-write cost from ~2-3s (ssh+osascript cold spawn each time) to
~200-400ms (skip SSH; one osascript spawn per request, but the interpreter
warms up after a few hits).

Endpoints (POST JSON bodies):

  POST /append   {sheet, col, date, value, src?}  # value like "+10" or "+'1n+'!S20"
  POST /write    {sheet, col, date|row, value, src?}  # set cell to value (literal or =formula)
  POST /read     {sheet, col, date|row}           # → {value, formula}
  POST /lookup   {sheet, date}                    # → {row}
  POST /batch    {sheet, date|row, appends:[{col,value,src?}], src?}  # N appends, one row lookup
  POST /ack      {sheet, col, date|row, note}     # bless the cell's CURRENT formula as the new ledger baseline
  GET  /health                                    # → {ok: true, version}

Sheet date-column resolution is hardcoded to match neon-cols.json:
  0分 → B,  0n → C,  1n+ → B,  hcbi → B

Bind to 127.0.0.1:9876 by default. Skills SSH to ix and curl localhost.

Audit ledger: every successful /append and /write is journaled as one JSONL
line in ~/vault/g245/neon-ledger/YYYY-MM.jsonl with the cell formula BEFORE
and AFTER the write, plus the caller-supplied `src` label. Entries chain: a
write whose observed before-formula doesn't match the ledger's last
after-formula for that cell means something wrote to the cell outside the
daemon (manual edit, stray osascript, sync clobber). The write still proceeds,
but the response carries "chain": "broken" plus "chain_expected" so callers
surface it immediately; scripts/neon-ledger-audit.py does the nightly replay.
Cells are keyed (sheet, col, date) — never raw row — so row insertions don't
poison history. /ack (with a mandatory note) blesses a deliberate manual edit.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.2.0"
ADDR = ("127.0.0.1", 9876)
EXCEL_LOCK = threading.Lock()  # serialize actual Excel/osascript calls across threads
TIMEOUT = 15  # osascript hard timeout
WORKBOOK = "Neon分v12.2.xlsx"
# Regression (2026-07-19, 07-20, 08-13, 08-14/15): every /write and /append
# kept returning 200 OK — cells really were updating in the open workbook —
# while Excel's AutoSave-to-OneDrive silently wedged for up to a full day.
# Nothing in this daemon ever called Save; it relied entirely on AutoSave, so
# a stalled AutoSave meant writes never reached disk/cloud until a human
# noticed the workbook's mtime hadn't moved. Force an explicit save on a
# timer so a wedge is bounded to SAVE_INTERVAL, not "however long until
# someone checks".
SAVE_INTERVAL = 120  # seconds

DATE_COL = {"0分": "B", "0n": "C", "1n+": "B", "hcbi": "B"}

LEDGER_DIR = os.path.expanduser("~/vault/g245/neon-ledger")
LEDGER_LOCK = threading.Lock()
# (sheet, col, anchor) → last after_formula the ledger recorded for that cell.
CHAIN_INDEX: dict[tuple[str, str, str], str] = {}


# ── Ledger ────────────────────────────────────────────────────────────────────

def chain_key(sheet: str, col: str, date: str | None, row: int | None) -> tuple[str, str, str]:
    """Ledger identity of a cell. Date-addressed writes key on the date so a
    row insertion in the sheet doesn't remap history; row-addressed writes
    (1n+ week cells) fall back to the row number."""
    anchor = date if date else f"r{row}"
    return (sheet, col, str(anchor))


def entry_key(e: dict) -> tuple[str, str, str]:
    return chain_key(e.get("sheet", ""), e.get("col", ""), e.get("date"), e.get("row"))


def ledger_path(when: datetime.datetime | None = None) -> str:
    when = when or datetime.datetime.now()
    return os.path.join(LEDGER_DIR, when.strftime("%Y-%m") + ".jsonl")


def iter_ledger(path: str):
    """Yield parsed entries, tolerating a torn/partial trailing line."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return


def seed_chain_index() -> None:
    """Rebuild the in-memory chain index from the previous + current month
    files (previous month covers restarts right after rollover)."""
    now = datetime.datetime.now()
    prev = (now.replace(day=1) - datetime.timedelta(days=1))
    CHAIN_INDEX.clear()
    for p in (ledger_path(prev), ledger_path(now)):
        for e in iter_ledger(p):
            after = e.get("after")
            if after is not None:
                CHAIN_INDEX[entry_key(e)] = after


def check_chain(key: tuple[str, str, str], before: str) -> tuple[str, str | None]:
    """→ (state, expected). state ∈ ok|broken|new. A miss rescans the current
    month file first: a fallback write (daemon was unreachable, client
    journaled directly) legitimately advances the chain behind our back."""
    expected = CHAIN_INDEX.get(key)
    if expected is None:
        return "new", None
    if before == expected:
        return "ok", None
    for e in iter_ledger(ledger_path()):
        if entry_key(e) == key and e.get("after") is not None:
            expected = e["after"]
    CHAIN_INDEX[key] = expected
    return ("ok", None) if before == expected else ("broken", expected)


def journal(entry: dict) -> None:
    """Append one ledger line and advance the chain index. Never raises."""
    try:
        with LEDGER_LOCK:
            os.makedirs(LEDGER_DIR, exist_ok=True)
            with open(ledger_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if entry.get("after") is not None:
            CHAIN_INDEX[entry_key(entry)] = entry["after"]
    except Exception as e:
        sys.stderr.write(f"ledger journal failed: {e}\n")


def osascript(script: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=TIMEOUT,
    )
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def save_workbook() -> tuple[int, str, str]:
    script = f'''
tell application "Microsoft Excel"
    save workbook "{WORKBOOK}"
end tell
'''
    return osascript(script)


def save_loop() -> None:
    """Background watchdog, started as a daemon thread from main(). Forces an
    explicit save every SAVE_INTERVAL seconds instead of trusting AutoSave."""
    while True:
        time.sleep(SAVE_INTERVAL)
        with EXCEL_LOCK:
            rc, _, err = save_workbook()
        if rc != 0:
            sys.stderr.write(f"save_loop: save failed: {err}\n")


def lookup_row(sheet: str, date_str: str) -> int | None:
    """Find the row in `sheet` whose date column matches `date_str` (M/D).

    The date column may hold EITHER an `M/D` text string (0分, hcbi, 1n+) OR a
    real Excel date value (0n col C), which AppleScript's `string value` renders
    as a long locale string like "Tuesday, June 30, 2026 …" — a plain `=`
    against "6/30" misses it. So compare a real date by its month/day and fall
    back to a text compare otherwise; empty cells are skipped."""
    dc = DATE_COL.get(sheet)
    if not dc:
        return None
    target = safe_str(date_str)
    script = f'''
tell application "Microsoft Excel"
    set theSheet to sheet "{sheet}" of workbook "{WORKBOOK}"
    repeat with i from 2 to 800
        set cv to value of cell ("{dc}" & i) of theSheet
        if cv is not missing value then
            if ((class of cv) as text) is "date" then
                set md to (((month of cv) as integer) as text) & "/" & ((day of cv) as text)
                if md = "{target}" then return i
            else
                if (cv as text) = "{target}" then return i
            end if
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


def _journal_and_respond(kind: str, req: dict, row: int,
                         before: str, value: str, formula: str) -> dict:
    """Common post-write path: chain-check the observed before-formula,
    journal the entry, and build the response."""
    sheet, col = req["sheet"], req["col"]
    date = req.get("date")
    key = chain_key(sheet, col, date, row)
    state, expected = check_chain(key, before)
    entry = {
        "ts": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "kind": kind, "sheet": sheet, "col": col, "row": row, "date": date,
        "value": str(req.get("value", "")), "before": before, "after": formula,
        "after_value": value, "src": req.get("src"), "chain": state,
    }
    if state == "broken":
        entry["chain_expected"] = expected
    journal(entry)
    resp = {"ok": True, "row": row, "col": col, "value": value,
            "formula": formula, "chain": state}
    if state == "broken":
        resp["chain_expected"] = expected
        resp["chain_hint"] = "cell was modified outside the daemon since its last ledger entry; if deliberate, POST /ack with a note"
    return resp


def do_batch(req: dict) -> dict:
    """N appends to one sheet/date in one HTTP round-trip:
    {sheet, date|row, appends: [{col, value, src?}], src?}. The row is
    resolved once; each append is journaled and chain-checked individually.
    Exists so did-fast's batch completion writes stay one network call."""
    sheet = req.get("sheet")
    appends = req.get("appends")
    if not sheet or not isinstance(appends, list) or not appends:
        return {"ok": False, "error": "missing_sheet_or_appends"}
    if req.get("row"):
        row = int(req["row"])
    else:
        row = lookup_row(sheet, req.get("date", ""))
        if row is None:
            return {"ok": False, "error": "date_not_found_or_missing_target"}
    results = []
    for item in appends:
        sub = {"sheet": sheet, "col": item.get("col"), "row": row,
               "date": req.get("date"), "value": item.get("value"),
               "src": item.get("src") or req.get("src")}
        if not sub["col"]:
            results.append({"ok": False, "error": "missing_col"})
            continue
        results.append(do_append(sub))
    ok = all(r.get("ok") for r in results)
    broken = [r["col"] for r in results if r.get("chain") == "broken"]
    out = {"ok": ok, "row": row, "results": results}
    if broken:
        out["chain_broken_cols"] = broken
    return out


def do_ack(req: dict) -> dict:
    """Bless the cell's current formula as the new chain baseline. `note` is
    mandatory — an ack without a reason is how real corruption gets laundered."""
    note = (req.get("note") or "").strip()
    if not note:
        return {"ok": False, "error": "ack_requires_note"}
    addr = cell_addr(req)
    if not addr:
        return {"ok": False, "error": "date_not_found_or_missing_target"}
    col, row = addr
    sheet = req["sheet"]
    script = f'''
tell application "Microsoft Excel"
    set theCell to cell ("{col}{row}") of sheet "{sheet}" of workbook "{WORKBOOK}"
    return ((value of theCell) as string) & (character id 9) & (formula of theCell)
end tell
'''
    rc, out, err = osascript(script)
    if rc != 0:
        return {"ok": False, "error": err}
    value, formula = (out.split("\t", 1) + [""])[:2]
    key = chain_key(sheet, col, req.get("date"), row)
    entry = {
        "ts": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "kind": "ack", "sheet": sheet, "col": col, "row": row,
        "date": req.get("date"), "value": None,
        "before": CHAIN_INDEX.get(key), "after": formula,
        "after_value": value, "src": req.get("src"), "note": note,
    }
    journal(entry)
    return {"ok": True, "row": row, "col": col, "value": value,
            "formula": formula, "superseded": entry["before"]}


def do_append(req: dict) -> dict:
    addr = cell_addr(req)
    if not addr:
        return {"ok": False, "error": "date_not_found_or_missing_target"}
    col, row = addr
    sheet = req["sheet"]
    val = str(req.get("value", ""))
    val_esc = safe_str(val)
    # "-" is a formula term like "+" (e.g. the 0t sleep dock appends "-7.5");
    # without it a negative append takes the string branch and produces
    # non-computing text on bare-number cells (same class as the 2026-07-14
    # "+2" regression below).
    is_numeric = val.lstrip().startswith(("+", "=", "-"))
    if is_numeric:
        empty_set = f'set formula of theCell to "={val_esc.lstrip("+")}"'
        # The existing cell may hold a bare number with no leading "="
        # (e.g. a plain value-set "2", not a formula). Concatenating "+2"
        # onto that directly produces the TEXT "2+2" instead of a formula,
        # so it silently stops summing (observed live 2026-07-14 on hcbi
        # Daily Dozen count cells). Normalize to a formula before appending.
        nonempty_set = (
            f'if oldFormula does not start with "=" then\n'
            f'        set formula of theCell to "=" & oldFormula & "{val_esc}"\n'
            f'    else\n'
            f'        set formula of theCell to oldFormula & "{val_esc}"\n'
            f'    end if'
        )
    else:
        clean_esc = safe_str(val.lstrip(", "))
        empty_set = f'set value of theCell to "{clean_esc}"'
        nonempty_set = f'set formula of theCell to oldFormula & "{val_esc}"'
    script = f'''
tell application "Microsoft Excel"
    set theSheet to sheet "{sheet}" of workbook "{WORKBOOK}"
    set theCell to cell ("{col}{row}") of theSheet
    set oldFormula to formula of theCell
    if oldFormula = "" or oldFormula = "0" then
        {empty_set}
    else
        {nonempty_set}
    end if
    return oldFormula & (character id 9) & ((value of theCell) as string) & (character id 9) & (formula of theCell)
end tell
'''
    rc, out, err = osascript(script)
    if rc != 0:
        return {"ok": False, "error": err}
    before, value, formula = (out.split("\t", 2) + ["", ""])[:3]
    return _journal_and_respond("append", req, row, before, value, formula)


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
    set theCell to cell ("{col}{row}") of sheet "{sheet}" of workbook "{WORKBOOK}"
    set oldFormula to formula of theCell
    set {setter} of theCell to "{val_esc}"
    return oldFormula & (character id 9) & ((value of theCell) as string) & (character id 9) & (formula of theCell)
end tell
'''
    rc, out, err = osascript(script)
    if rc != 0:
        return {"ok": False, "error": err}
    before, value, formula = (out.split("\t", 2) + ["", ""])[:3]
    return _journal_and_respond("write", req, row, before, value, formula)


def do_read(req: dict) -> dict:
    addr = cell_addr(req)
    if not addr:
        return {"ok": False, "error": "date_not_found_or_missing_target"}
    col, row = addr
    sheet = req["sheet"]
    script = f'''
tell application "Microsoft Excel"
    set theCell to cell ("{col}{row}") of sheet "{sheet}" of workbook "{WORKBOOK}"
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
    "/batch":  do_batch,
    "/ack":    do_ack,
}


class Handler(BaseHTTPRequestHandler):
    # Regression (2026-07-15, recurred twice same day): the server was
    # single-threaded with no socket timeout. A stalled client connection
    # (e.g. our own curl hitting its own --max-time and giving up client-side
    # while the server's blocking rfile.read() waited forever for bytes that
    # were never coming) wedged the ONE request-handling thread permanently —
    # every subsequent request queued forever, looking identical to "daemon
    # down" even though the process was alive and the port was LISTENing.
    # `timeout` makes a stalled read give up instead of hanging forever.
    timeout = 20

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
            # Threads must not send concurrent AppleEvents to Excel — serialize
            # the actual Excel-touching call; the HTTP layer above stays
            # threaded so a stalled connection can't block other requests.
            with EXCEL_LOCK:
                result = handler(body)
        except subprocess.TimeoutExpired:
            return self._send(504, {"ok": False, "error": "osascript_timeout"})
        except Exception as e:
            return self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        return self._send(200 if result.get("ok") else 400, result)


def main():
    seed_chain_index()
    print(f"ledger chain index seeded: {len(CHAIN_INDEX)} cells", flush=True)
    threading.Thread(target=save_loop, daemon=True).start()
    # ThreadingHTTPServer so one stuck/slow request (a stalled client, a slow
    # Excel call) can't block every other request behind it — see Handler.timeout.
    srv = ThreadingHTTPServer(ADDR, Handler)
    srv.daemon_threads = True
    print(f"excel-http v{VERSION} listening on {ADDR[0]}:{ADDR[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.server_close()


if __name__ == "__main__":
    main()
