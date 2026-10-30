#!/usr/bin/env python3
"""
excel-http — tiny localhost daemon on ix that fronts AppleScript writes to Neon.

Cuts the per-write cost from ~2-3s (ssh+osascript cold spawn each time) to
~200-400ms (skip SSH; one osascript spawn per request, but the interpreter
warms up after a few hits).

Endpoints (POST JSON bodies):

  POST /append   {sheet, col, date, value, src?, idem?}  # value like "+10" or "+'1n+'!S20"
  POST /write    {sheet, col, date|row, value, src?}  # set cell to value (literal or =formula)
  POST /read     {sheet, col, date|row}           # → {value, formula}
  POST /lookup   {sheet, date}                    # → {row}
  POST /batch    {sheet, date|row, appends:[{col,value,src?}], src?}  # N appends, one row lookup
  POST /ack      {sheet, col, date|row, note}     # bless the cell's CURRENT formula as the new ledger baseline
  POST /reconcile {sheet, date|row, cols:[...]}   # journal outside (Excel) edits as source 3p-app
  POST /observe  {sheet, ts, cells:[...]}          # manual edits from the Neon audit macro (no Excel call)
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

import contextlib
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.11.0"
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

DATE_COL = {"0分": "B", "0n": "C", "1n+": "B", "hcbi": "B",
            # 2026-10-19 (step 2c): review sheets. 0s897!B holds real dates
            # spanning 2026-01-01..2027-01-11 (callers pass M/D/YY); the two
            # weekly sheets hold unique float week labels in col A ("10.3").
            "0s897": "B", "1分+1s": "A", "i9+m5x2": "A"}
# Sheets addressed by a week label rather than a date: journal row-anchored
# (chain_key r<row>) with the label kept as `week`, never as `date`.
LABEL_SHEETS = {"1n+", "1分+1s", "i9+m5x2"}

LEDGER_DIR = os.path.expanduser("~/vault/g245/neon-ledger")
LEDGER_LOCK = threading.Lock()
# Neon-off-Excel step 1 (2026-10-19): every journaled entry is ALSO recorded
# in the semantic SQLite ledger (lib/neon/ledger.py, ~/.local/share/neon).
# Guarded import: a broken ledger module must never take the daemon down,
# but the failure is loud (stderr + /health "ledger" field) so a silently
# disabled double-write can't go unnoticed.
_REPO_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib")
sys.path.insert(0, os.path.normpath(_REPO_LIB))
try:
    from neon import ledger as neon_ledger  # noqa: E402
    LEDGER_DB_STATE: dict = {"ok": True, "db": str(neon_ledger.DB_PATH), "errors": 0}
except Exception as _e:  # pragma: no cover - import environment
    neon_ledger = None
    LEDGER_DB_STATE = {"ok": False, "error": f"{type(_e).__name__}: {_e}", "errors": 0}
    sys.stderr.write(f"semantic ledger import failed: {_e}\n")
# (sheet, col, anchor) → last after_formula the ledger recorded for that cell.
CHAIN_INDEX: dict[tuple[str, str, str], str] = {}
# idem key → after-formula of the append that first carried it. A caller that
# can be killed after its write lands (did-fast's 60s watchdog) and is then
# retried by dtd sends the same key again; see idem_dup().
IDEM_INDEX: dict[str, str] = {}


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
    IDEM_INDEX.clear()
    for p in (ledger_path(prev), ledger_path(now)):
        for e in iter_ledger(p):
            after = e.get("after")
            if after is not None:
                CHAIN_INDEX[entry_key(e)] = after
                if e.get("idem"):
                    IDEM_INDEX[e["idem"]] = after


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
            if entry.get("idem"):
                IDEM_INDEX[entry["idem"]] = entry["after"]
    except Exception as e:
        sys.stderr.write(f"ledger journal failed: {e}\n")
    double_write(entry)


def double_write(entry: dict) -> None:
    """Mirror a journaled entry into the semantic SQLite ledger. Runs after
    the Excel write and the JSONL append have both landed, so it can only
    ever log — a DB hiccup must not turn an already-applied write into a
    500 (the caller would retry and double-credit)."""
    if neon_ledger is None:
        return
    try:
        neon_ledger.record(entry)
    except Exception as e:
        LEDGER_DB_STATE["errors"] = LEDGER_DB_STATE.get("errors", 0) + 1
        LEDGER_DB_STATE["last_error"] = f"{type(e).__name__}: {e}"
        sys.stderr.write(f"semantic ledger write failed: {e}\n")


QUIET_LOCK = "/tmp/neon-osa-quiet.lock"  # shared with scripts/neon-osa-quiet.sh
_EVENTS = 'tell application "Microsoft Excel" to set enable events to {}'


def _is_write(script: str) -> bool:
    return "set value" in script or "set formula" in script


@contextlib.contextmanager
def quiet_excel():
    """Excel events off for a pipeline write, so the Neon audit macro (VBA
    SheetChange) only logs edits made in Excel itself. Holds the same lock as
    scripts/neon-osa-quiet.sh so no other writer can re-enable events
    mid-write, and re-enables from Python even if osascript is killed."""
    with open(QUIET_LOCK, "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            subprocess.run(["osascript", "-e", _EVENTS.format("false")],
                           capture_output=True, timeout=TIMEOUT)
            yield
        finally:
            subprocess.run(["osascript", "-e", _EVENTS.format("true")],
                           capture_output=True, timeout=TIMEOUT)
            fcntl.flock(lk, fcntl.LOCK_UN)


def osascript(script: str) -> tuple[int, str, str]:
    ctx = quiet_excel() if _is_write(script) else contextlib.nullcontext()
    with ctx:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    # Only the trailing newline comes off. A full strip() ate the leading
    # "\t\t" that /write and /batch_write echo for a blank cell (old formula
    # and old value both ""), shifting every field left: the ledger recorded
    # before=<new value>, after="", and did-fast's verify saw "" for the value
    # it had just written. Every first mark of the day failed and only the
    # retry passed (2026-10-21, every 0n habit column that morning).
    return r.returncode, r.stdout.rstrip("\r\n"), r.stderr.strip()


# Neon audit macro liveness, reported on /health. last_ping comes from the
# watchdog's `run VB macro "NeonAuditPing"` round trip.
MACRO_STATE: dict[str, str | None] = {"last_ping": None, "last_observe": None}
MACRO_INSTALLED = os.path.expanduser("~/.config/neon-audit/installed")


def ping_macro() -> None:
    """Re-arm the audit macro (a VBA reset leaves it dead) and have it ping
    back through /observe, proving the whole Excel → AppleScriptTask → curl
    path works. Gated on MACRO_INSTALLED: calling a macro Excel doesn't have
    can raise a modal error dialog, which would wedge every write."""
    if not os.path.exists(MACRO_INSTALLED):
        return
    subprocess.run(["osascript", "-e",
                    'tell application "Microsoft Excel" to run VB macro "NeonAuditPing"'],
                   capture_output=True, timeout=TIMEOUT)


def ensure_events_on() -> None:
    """Watchdog: a writer killed mid-write (or a crash) can leave Excel events
    off for the whole app, silently disabling the audit macro. Re-enable under
    the writer lock so it can't fire in the middle of someone's quiet write."""
    try:
        with open(QUIET_LOCK, "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                subprocess.run(["osascript", "-e", _EVENTS.format("true")],
                               capture_output=True, timeout=TIMEOUT)
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)
    except Exception as e:
        sys.stderr.write(f"ensure_events_on: {e}\n")


def save_workbook() -> tuple[int, str, str]:
    script = f'''
tell application "Microsoft Excel"
    save workbook "{WORKBOOK}"
end tell
'''
    return osascript(script)


def recalc_guard() -> str:
    """Catch Excel's dependency recalculation stalling (2026-10-22): from ~14:17
    to 19:47 every write landed in its own cell (0分!R climbed 454 → 661) but
    nothing downstream recalculated, so Σ (D) sat at 1099 while P:Y summed to
    1382, Janus showed no points after 申, and the 18:00 block lock recorded 酉
    as an empty block. `calculate` did nothing; `calculate full` fixed it.
    D is =SUM(P:Y) by definition, so today's D disagreeing with the live sum
    is a cheap, exact stall signal. Returns 'ok', 'skip', or 'STALE d s'."""
    now = datetime.datetime.now()
    row = lookup_row_cached("0分", f"{now.month}/{now.day}")
    if not row:
        return "skip"
    script = f'''
tell application "Microsoft Excel"
    set ws to sheet "0分" of workbook "{WORKBOOK}"
    if (formula of cell "D{row}" of ws) is not "=SUM(P{row}:Y{row})" then return "skip"
    set d to value of cell "D{row}" of ws
    set vals to value of range "P{row}:Y{row}" of ws
    set row1 to item 1 of vals
    set s to 0
    -- indexed, not `repeat with v in`: that yields references and `v as real` fails
    repeat with i from 1 to count of row1
        try
            set s to s + ((item i of row1) as real)
        end try
    end repeat
    if (d - s) > 0.5 or (s - d) > 0.5 then
        calculate full
        return "STALE " & (d as string) & " " & (s as string)
    end if
    return "ok"
end tell
'''
    rc, out, err = osascript(script)
    return out.strip() if rc == 0 else f"error {err.strip()[:120]}"


def save_loop() -> None:
    """Background watchdog, started as a daemon thread from main(). Forces an
    explicit save every SAVE_INTERVAL seconds instead of trusting AutoSave."""
    while True:
        time.sleep(SAVE_INTERVAL)
        # Nothing may escape this body (bug 2026-10-22): one save that ran past
        # osascript's 15 s timeout raised TimeoutExpired, killed this thread,
        # and with it every later save. Excel on Ix kept taking writes but its
        # co-authoring upload stopped at 13:26, so the web copy went stale while
        # the daemon looked healthy. A failed pass just waits for the next one.
        try:
            with EXCEL_LOCK:
                rc, _, err = save_workbook()
                ensure_events_on()
                try:
                    ping_macro()
                except Exception as e:
                    sys.stderr.write(f"ping_macro: {e}\n")
                try:
                    g = recalc_guard()
                    if g.startswith("STALE"):
                        sys.stderr.write(f"recalc_guard: Σ stale, ran calculate full ({g})\n")
                except Exception as e:
                    sys.stderr.write(f"recalc_guard: {e!r}\n")
            if rc != 0:
                sys.stderr.write(f"save_loop: save failed: {err}\n")
        except Exception as e:
            sys.stderr.write(f"save_loop: pass failed, retrying next interval: {e!r}\n")


_DATE_FORMS = (
    re.compile(r"^(\d{1,2})/(\d{1,2})$"),                       # M/D
    re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$"),         # M/D/YY, M/D/YYYY
    re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"),                   # YYYY-MM-DD
)


def date_target(date_str) -> tuple[str, int | None]:
    """("M/D", year|None) for a date-shaped target; anything else (a week
    label like "10.3") comes back unchanged with year None. A year makes a
    real-date cell match only in that year (0s897!B spans two Januaries)."""
    s = str(date_str).strip()
    m = _DATE_FORMS[0].match(s)
    if m:
        return f"{int(m.group(1))}/{int(m.group(2))}", None
    m = _DATE_FORMS[1].match(s)
    if m:
        y = int(m.group(3))
        return f"{int(m.group(1))}/{int(m.group(2))}", (2000 + y if y < 100 else y)
    m = _DATE_FORMS[2].match(s)
    if m:
        return f"{int(m.group(2))}/{int(m.group(3))}", int(m.group(1))
    return s, None


def _date_match_as(md: str, year: int | None, text_target: str) -> str:
    """AppleScript predicate body: `cv` already bound; sets `hit` to true/false."""
    year_test = f" and ((year of cv) as integer) = {year}" if year else ""
    return (f'if ((class of cv) as text) is "date" then\n'
            f'            set md to (((month of cv) as integer) as text) & "/" & ((day of cv) as text)\n'
            f'            set hit to (md = "{safe_str(md)}"{year_test})\n'
            f'        else\n'
            f'            set hit to ((cv as text) = "{safe_str(text_target)}")\n'
            f'        end if')


def lookup_row(sheet: str, date_str: str) -> int | None:
    """Find the row in `sheet` whose date column matches `date_str` (M/D,
    M/D/YY, YYYY-MM-DD, or a week label).

    The date column may hold EITHER an `M/D` text string (0分, hcbi, 1n+) OR a
    real Excel date value (0n col C), which AppleScript's `string value` renders
    as a long locale string like "Tuesday, June 30, 2026 …" — a plain `=`
    against "6/30" misses it. So compare a real date by its month/day and fall
    back to a text compare otherwise; empty cells are skipped."""
    dc = DATE_COL.get(sheet)
    if not dc:
        return None
    md, year = date_target(date_str)
    # Bulk-fetch the whole date column in ONE Apple Event, then match locally
    # in the AppleScript interpreter (no further IPC to Excel per row).
    # The old version did `value of cell` in a `repeat ... to 800` loop — up
    # to 799 separate Apple Event round trips, one per row. At row 225 (a
    # typical mid-year date row) that alone cost ~4.2s and dominated janus
    # mobile's ~30s page-load report (confirmed 2026-08-15). Bulk-range read
    # + local scan resolves the same row in ~0.18s.
    script = f'''
tell application "Microsoft Excel"
    set theSheet to sheet "{sheet}" of workbook "{WORKBOOK}"
    set colVals to value of range ("{dc}2:{dc}800") of theSheet
    repeat with i from 1 to count of colVals
        set cv to item 1 of item i of colVals
        if cv is not missing value then
        {_date_match_as(md, year, str(date_str).strip())}
            if hit then return (i + 1)
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


# (sheet, date_str) -> row. Every /append and /write re-ran lookup_row's full
# ~800-row bulk-range scan (~0.18s) even for the SAME date as the previous
# call in the same burst (e.g. /ate's four writes, /-1g's build-order +
# Todoist + cache-refresh flow, janus's new +N posthoc command) — the exact
# repeat-call pattern this daemon's callers actually produce. Safe without
# its own lock: do_POST already holds EXCEL_LOCK around the whole handler
# call, so only one request's body ever executes at a time.
_ROW_CACHE: dict[tuple[str, str], int] = {}


def _row_still_matches(sheet: str, row: int, date_str: str) -> bool:
    """Single-cell re-check that `row`'s date cell still reads as `date_str`
    — cheap (one cell, not an 800-row range) insurance against a cached row
    going stale if a row got inserted/deleted in the sheet between calls.
    Mirrors lookup_row's own real-date-vs-text comparison."""
    dc = DATE_COL.get(sheet)
    if not dc:
        return False
    md, year = date_target(date_str)
    script = f'''
tell application "Microsoft Excel"
    set theSheet to sheet "{sheet}" of workbook "{WORKBOOK}"
    set cv to value of cell ("{dc}" & {row}) of theSheet
    if cv is missing value then return "0"
        {_date_match_as(md, year, str(date_str).strip())}
    if hit then return "1"
    return "0"
end tell
'''
    rc, out, _ = osascript(script)
    return rc == 0 and out.strip() == "1"


def lookup_row_cached(sheet: str, date_str: str) -> int | None:
    """lookup_row, but skipping the full range-scan on a cache hit — one
    cheap single-cell verify instead. Populates/evicts _ROW_CACHE."""
    key = (sheet, date_str)
    cached = _ROW_CACHE.get(key)
    if cached is not None:
        if _row_still_matches(sheet, cached, date_str):
            return cached
        del _ROW_CACHE[key]
    row = lookup_row(sheet, date_str)
    if row is not None:
        _ROW_CACHE[key] = row
    return row


def safe_str(s: str) -> str:
    """Escape backslashes and double quotes for embedding inside an AS string literal."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def as_literal(s) -> str:
    """Prose-safe AppleScript string literal body: CRLF/CR normalised to LF,
    each line escaped, lines joined with `" & linefeed & "` (Excel for Mac
    cells use LF; raw newlines inside a literal also work, this is just
    explicit). Used for review-sheet text (0s897, 1分+1s, i9+m5x2)."""
    text = str(s).replace("\r\n", "\n").replace("\r", "")
    return '" & linefeed & "'.join(safe_str(line) for line in text.split("\n"))


def cell_addr(req: dict) -> tuple[str, int] | None:
    """Resolve the (col, row) target from the request body."""
    sheet = req.get("sheet")
    col = req.get("col")
    if not sheet or not col:
        return None
    if "row" in req and req["row"]:
        return col, int(req["row"])
    if "date" in req and req["date"]:
        r = lookup_row_cached(sheet, req["date"])
        if r is None:
            return None
        return col, r
    return None


def _journal_and_respond(kind: str, req: dict, row: int,
                         before: str, value: str, formula: str,
                         before_value: str | None = None) -> dict:
    """Common post-write path: chain-check the observed before-formula,
    journal the entry, and build the response."""
    sheet, col = req["sheet"], req["col"]
    date = req.get("date")
    key = chain_key(sheet, col, date, row)
    state, expected = check_chain(key, before)
    if state != "ok":
        # The cell may have last been written under its other key (ritual
        # -1n writes are row-addressed, did-fast date-addressed): judge it
        # against the newest entry across both keys.
        last = latest_entry(sheet, col, date, row)
        if last is not None:
            if last.get("after") == before:
                state, expected = "ok", None
            else:
                state, expected = "broken", last.get("after")
    entry = {
        "ts": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "kind": kind, "sheet": sheet, "col": col, "row": row, "date": date,
        "value": str(req.get("value", "")), "before": before, "after": formula,
        "after_value": value, "src": req.get("src"), "chain": state,
    }
    if before_value is not None:
        # With the previous entry's after_value, a broken chain's
        # before_value - expected value is the outside (Excel) edit in points.
        entry["before_value"] = before_value
    for k in ("source", "via", "idem", "week"):  # source/via: JM Dash dimension (lib/jmsource)
        if req.get(k):
            entry[k] = req[k]
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
        row = lookup_row_cached(sheet, req.get("date", ""))
        if row is None:
            return {"ok": False, "error": "date_not_found_or_missing_target"}
    results = []
    for item in appends:
        sub = {"sheet": sheet, "col": item.get("col"), "row": row,
               "date": req.get("date"), "value": item.get("value"),
               "idem": item.get("idem"),
               "src": item.get("src") or req.get("src"),
               "source": req.get("source"), "via": req.get("via")}
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


def _as_setter(value) -> str:
    """AppleScript RHS + setter for a /batch_write value: JSON numbers land as
    numbers (0n habit marks: 1, minutes, HHMM), "=..." as formulas, anything
    else as a quoted string."""
    if isinstance(value, bool):
        return f'set value of theCell to {str(value).lower()}'
    if isinstance(value, (int, float)):
        return f'set value of theCell to {value}'
    sval = str(value)
    if sval.startswith("="):
        return f'set formula of theCell to "{safe_str(sval)}"'
    return f'set value of theCell to "{as_literal(sval)}"'


def _as_text_setter(value) -> str:
    """mode "text": always a value, never a formula (prose may start with
    "=" or "-"); never routed through the append branch."""
    return f'set value of theCell to "{as_literal(value)}"'


def do_batch_write(req: dict) -> dict:
    """N value/formula writes to one sheet/date in ONE osascript call:
    {sheet, date|row, writes: [{col, value, mode?: "set"|"add"|"append", src?}], src?}.
    `mode: "add"` adds to a numeric cell (0n cumulative habits: 问学, xk20…),
    first write on a blank/0 cell behaves like "set"; `mode: "append"` is
    /append's formula concatenation ("+45" → `=old+45`, journaled as kind
    append) for 1n+ minutes and hcbi. Each cell is journaled
    (and double-written to the semantic ledger) individually, with its
    pre-image, and echoed back so the caller can verify what landed.
    Exists so did-fast's habit completions (previously a raw ix-osa
    AppleScript, unjournaled: Neon-off-Excel step 2, 2026-10-19) stay one
    round-trip while gaining the ledger."""
    sheet = req.get("sheet")
    writes = req.get("writes")
    if not sheet or not isinstance(writes, list) or not writes:
        return {"ok": False, "error": "missing_sheet_or_writes"}
    if req.get("row"):
        row = int(req["row"])
    else:
        row = lookup_row_cached(sheet, req.get("date", ""))
        if row is None:
            return {"ok": False, "error": "date_not_found_or_missing_target"}
    items = []
    for w in writes:
        col = w.get("col")
        if not col or not re.fullmatch(r"[A-Za-z]{1,3}", str(col)):
            return {"ok": False, "error": f"bad_col:{col!r}"}
        items.append((str(col).upper(), w.get("value", ""), (w.get("mode") or "set"), w))
    blocks = []
    for col, value, mode, _ in items:
        setter = _as_setter(value)
        if mode == "add" and isinstance(value, (int, float)) and not isinstance(value, bool):
            setter = (f'if oldValue = "" or oldValue = "0" then\n'
                      f'        set value of theCell to {value}\n'
                      f'    else\n'
                      f'        set value of theCell to (oldValue as number) + {value}\n'
                      f'    end if')
        elif mode == "text":
            setter = _as_text_setter(value)
        elif mode == "append":
            # Formula append with /append's exact normalization (shared
            # fragment): 1n+ cumulative/variable minutes, hcbi "+N".
            empty_set, nonempty_set = _append_setters(str(value))
            setter = (f'if oldFormula = "" or oldFormula = "0" then\n'
                      f'        {empty_set}\n'
                      f'    else\n'
                      f'        {nonempty_set}\n'
                      f'    end if')
        blocks.append(f'''
    set theCell to cell ("{col}{row}") of theSheet
    set oldFormula to formula of theCell
    set oldValue to (value of theCell) as string
    {setter}
    set out to out & oldFormula & (character id 9) & oldValue & (character id 9) & ((value of theCell) as string) & (character id 9) & (formula of theCell) & (character id 30)''')
    script = f'''
tell application "Microsoft Excel"
    set theSheet to sheet "{sheet}" of workbook "{WORKBOOK}"
    set out to ""{"".join(blocks)}
    return out
end tell
'''
    rc, out, err = osascript(script)
    if rc != 0:
        return {"ok": False, "error": err}
    cells = [c for c in out.split("\x1e") if c != ""]
    if len(cells) != len(items):
        return {"ok": False, "error": f"echo_mismatch:{len(cells)}/{len(items)}", "raw": out[:200]}
    results = []
    for (col, value, mode, w), cell in zip(items, cells):
        before, before_value, new_value, formula = (cell.split("\t", 3) + ["", "", ""])[:4]
        sub = {"sheet": sheet, "col": col, "row": row, "date": req.get("date"),
               "value": value, "src": w.get("src") or req.get("src"),
               "source": req.get("source"), "via": req.get("via")}
        if sheet in LABEL_SHEETS:
            # The "date" that resolved the row is a week label ("10.3"), not
            # an M/D: journal row-anchored like every existing 1n+ entry
            # (chain_key r<row>) and keep the label as `week`.
            sub["date"], sub["week"] = None, req.get("date")
        r = _journal_and_respond("append" if mode == "append" else "write",
                                 sub, row, before, new_value, formula, before_value)
        r["before"] = before
        r["before_value"] = before_value
        r["mode"] = mode
        results.append(r)
    resp = {"ok": all(r.get("ok") for r in results), "row": row, "results": results}
    broken = [r["col"] for r in results if r.get("chain") == "broken"]
    if broken:
        resp["chain_broken_cols"] = broken
    return resp


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


def idem_dup(idem: str | None, formula: str, value: str) -> bool:
    """True when an append carrying `idem` already landed and is still in the
    cell, i.e. this request is a replay, not a new credit.

    Regression 2026-10-12: a dtd ⌥↵ on "validate no yt [25]" ran did-fast
    while Excel was slow; the +25 landed at 56s, the 60s watchdog then killed
    did-fast (exit 124), and dtd's retry queue re-drove the "failed"
    completion twice: +25 three times in 0分!U. "Still in the cell" = the
    current formula extends the formula that append produced, without the
    negation undo-fast appends when it can't strip the tail. So ctrl-z
    (strip or negate) followed by a re-complete credits again."""
    prev = IDEM_INDEX.get(idem) if idem else None
    if prev is None or not formula.startswith(prev):
        return False
    neg = "-" + value.lstrip("+")
    return neg not in formula[len(prev):]


def _read_cell(sheet: str, col: str, row: int) -> tuple[str, str] | None:
    """(value, formula) of one cell, or None if Excel didn't answer."""
    rc, out, _ = osascript(f'''
tell application "Microsoft Excel"
    set theCell to cell ("{col}{row}") of sheet "{sheet}" of workbook "{WORKBOOK}"
    return ((value of theCell) as string) & (character id 9) & (formula of theCell)
end tell
''')
    if rc != 0:
        return None
    value, formula = (out.split("\t", 1) + [""])[:2]
    return value, formula


def _append_setters(val: str) -> tuple[str, str]:
    """(empty_set, nonempty_set) AppleScript for appending `val` to theCell
    whose current formula is `oldFormula`. ONE implementation for /append and
    /batch_write mode "append" so the 2026-07-14 bare-number normalization
    can never drift between them."""
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
    return empty_set, nonempty_set


def do_append(req: dict) -> dict:
    addr = cell_addr(req)
    if not addr:
        return {"ok": False, "error": "date_not_found_or_missing_target"}
    col, row = addr
    sheet = req["sheet"]
    idem = req.get("idem")
    if idem and idem in IDEM_INDEX:
        cur = _read_cell(sheet, col, row)
        if cur and idem_dup(idem, cur[1], str(req.get("value", ""))):
            sys.stderr.write(f"idem dup skipped: {idem}\n")
            return {"ok": True, "row": row, "col": col, "value": cur[0],
                    "formula": cur[1], "chain": "ok", "dup": True}
    val = str(req.get("value", ""))
    empty_set, nonempty_set = _append_setters(val)
    script = f'''
tell application "Microsoft Excel"
    set theSheet to sheet "{sheet}" of workbook "{WORKBOOK}"
    set theCell to cell ("{col}{row}") of theSheet
    set oldFormula to formula of theCell
    set oldValue to (value of theCell) as string
    if oldFormula = "" or oldFormula = "0" then
        {empty_set}
    else
        {nonempty_set}
    end if
    return oldFormula & (character id 9) & oldValue & (character id 9) & ((value of theCell) as string) & (character id 9) & (formula of theCell)
end tell
'''
    rc, out, err = osascript(script)
    if rc != 0:
        return {"ok": False, "error": err}
    before, before_value, value, formula = (out.split("\t", 3) + ["", "", ""])[:4]
    return _journal_and_respond("append", req, row, before, value, formula, before_value)


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
    set oldValue to (value of theCell) as string
    set {setter} of theCell to "{val_esc}"
    return oldFormula & (character id 9) & oldValue & (character id 9) & ((value of theCell) as string) & (character id 9) & (formula of theCell)
end tell
'''
    rc, out, err = osascript(script)
    if rc != 0:
        return {"ok": False, "error": err}
    before, before_value, value, formula = (out.split("\t", 3) + ["", "", ""])[:4]
    return _journal_and_respond("write", req, row, before, value, formula, before_value)


def latest_entry(sheet: str, col: str, date: str | None, row: int) -> dict | None:
    """Newest ledger entry (prev + current month) for this cell under either
    its date key or its row key, or None if the ledger has never seen it."""
    keys = {chain_key(sheet, col, None, row)}
    if date:
        keys.add(chain_key(sheet, col, date, row))
    now = datetime.datetime.now()
    prev = now.replace(day=1) - datetime.timedelta(days=1)
    found = None
    for p in (ledger_path(prev), ledger_path(now)):
        for e in iter_ledger(p):
            if entry_key(e) in keys and e.get("after") is not None:
                found = e  # file order is time order
    return found


def do_reconcile(req: dict) -> dict:
    """Journal outside edits the pipeline never wrote over: {sheet, date, cols}.

    Reads each cell once. A cell whose formula differs from the ledger's last
    after-formula gets a `reconcile` entry attributed to source 3p-app / via
    excel. A cell the ledger has never seen gets a sourceless `baseline` entry
    instead (its origin is unknown: row-template formulas look the same as
    edits), so run it early each morning too, before the day's edits. That records the
    Excel edit with before_value/after_value so JM Dash can count its points,
    and advances the chain so the next pipeline write isn't flagged broken for
    an edit that's already accounted for. Run nightly (scripts/neon-reconcile.sh)."""
    cols = req.get("cols") or []
    sheet = req.get("sheet")
    if not sheet or not cols:
        return {"ok": False, "error": "missing_sheet_or_cols"}
    addr = cell_addr({"sheet": sheet, "col": cols[0], "date": req.get("date"), "row": req.get("row")})
    if not addr:
        return {"ok": False, "error": "date_not_found_or_missing_target"}
    row = addr[1]
    reads = "\n".join(
        f'    set c to cell ("{safe_str(c)}{row}") of theSheet\n'
        f'    set out to out & ((value of c) as string) & (character id 9) & (formula of c) & (character id 30)'
        for c in cols)
    script = f'''
tell application "Microsoft Excel"
    set theSheet to sheet "{sheet}" of workbook "{WORKBOOK}"
    set out to ""
{reads}
    return out
end tell
'''
    rc, out, err = osascript(script)
    if rc != 0:
        return {"ok": False, "error": err}
    cells = out.split("\x1e")
    changes = []
    for col, cell in zip(cols, cells):
        value, formula = (cell.split("\t", 1) + [""])[:2]
        # A cell is chained under its date key (date-addressed writes) or its
        # row key (row-addressed writes, e.g. ritual -1n); compare against the
        # newest of the two and journal under that same addressing so the
        # next write on either path chains cleanly.
        last = latest_entry(sheet, col, req.get("date"), row)
        if last is not None and last.get("after") == formula:
            # Same formula. With "snapshot": true, still journal a VALUE change
            # (kind snapshot): a formula cell like ='0n'!BK292 moves whenever a
            # habit is marked, and nothing else ever records that value (the
            # rule-engine parallel run needs it, 2026-10-19). Never kind
            # reconcile: JM Dash scores reconcile deltas as Excel-edit points.
            if not req.get("snapshot") or (last.get("after_value") or "") == (value or ""):
                continue
            journal({
                "ts": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "kind": "snapshot", "sheet": sheet, "col": col, "row": row,
                "date": last.get("date") or req.get("date"), "value": None,
                "before": formula, "before_value": last.get("after_value"),
                "after": formula, "after_value": value, "src": req.get("src") or "snapshot",
            })
            changes.append({"col": col, "kind": "snapshot",
                            "before_value": last.get("after_value"), "after_value": value})
            continue
        entry = {
            "ts": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "sheet": sheet, "col": col, "row": row,
            "date": last.get("date") if last is not None else req.get("date"),
            "value": None,
            "before": last.get("after") if last is not None else None,
            "before_value": last.get("after_value") if last is not None else None,
            "after": formula, "after_value": value,
        }
        if last is None:
            # Unknown history (row template formulas, pre-ledger values):
            # record a sourceless starting point, never an Excel edit.
            entry.update(kind="baseline", src=req.get("src") or "baseline")
        else:
            entry.update(kind="reconcile", src=req.get("src") or "reconcile",
                         source="3p-app", via="excel")
        journal(entry)
        changes.append({"col": col, "kind": entry["kind"],
                        "before_value": entry["before_value"], "after_value": value})
    return {"ok": True, "row": row, "changes": changes}


def do_observe(req: dict) -> dict:
    """Manual Excel edits reported by the Neon audit macro (VBA SheetChange →
    AppleScriptTask → curl): {sheet, ts, structural?, cells: [{row, col,
    date, formula, value, before_formula?, before_value?}]}.

    Never touches Excel (the macro's Excel is waiting on nothing, but keep it
    that way) and runs outside EXCEL_LOCK. Pipeline writes run with events
    off, so everything arriving here was done in Excel: source 3p-app, via
    excel. An observe that arrives after a newer ledger entry for the same
    cell (a pipeline write raced it) is journaled with `observed_after`
    instead of `after`, so it can't roll the chain back."""
    if req.get("ping"):
        MACRO_STATE["last_ping"] = datetime.datetime.now().isoformat(timespec="seconds")
        return {"ok": True, "ping": True}
    sheet = req.get("sheet")
    if not sheet:
        return {"ok": False, "error": "missing_sheet"}
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    client_ts = req.get("ts") or now
    MACRO_STATE["last_observe"] = now
    if req.get("structural"):
        _ROW_CACHE.clear()  # inserted/deleted rows shift every row lookup
        journal({"ts": now, "client_ts": client_ts, "kind": "structural",
                 "sheet": sheet, "rows": req.get("rows"), "after": None,
                 "source": "3p-app", "via": "excel"})
        return {"ok": True, "structural": True}
    cells = req.get("cells") or []
    latest = latest_entries(sheet, cells)
    n = 0
    for c in cells:
        col, row = c.get("col"), c.get("row")
        if not col or not row:
            continue
        formula = c.get("formula", "")
        last = latest.get((col, int(row)))
        before = c.get("before_formula", last.get("after") if last else None)
        if before == formula:
            continue  # re-entered the same thing; nothing changed
        entry = {
            "ts": now, "client_ts": client_ts, "kind": "excel-edit",
            "sheet": sheet, "col": col, "row": int(row),
            "date": (last.get("date") if last else None) or c.get("date"),
            "value": None, "before": before,
            "before_value": c.get("before_value", last.get("after_value") if last else None),
            "after_value": c.get("value"),
            "src": "excel", "source": "3p-app", "via": "excel",
        }
        if last is not None and (last.get("ts") or "") > client_ts:
            entry["after"], entry["observed_after"] = None, formula
        else:
            entry["after"] = formula
        journal(entry)
        n += 1
    return {"ok": True, "journaled": n, "truncated": bool(req.get("truncated"))}


def latest_entries(sheet: str, cells: list) -> dict:
    """{(col, row): newest ledger entry} for many cells in one ledger pass,
    matching each cell under its row key or (if the macro sent one) date key."""
    want = {}
    for c in cells:
        if c.get("col") and c.get("row"):
            r = int(c["row"])
            want[chain_key(sheet, c["col"], None, r)] = (c["col"], r)
            if c.get("date"):
                want[chain_key(sheet, c["col"], c["date"], r)] = (c["col"], r)
    now = datetime.datetime.now()
    prev = now.replace(day=1) - datetime.timedelta(days=1)
    found = {}
    for p in (ledger_path(prev), ledger_path(now)):
        for e in iter_ledger(p):
            cell = want.get(entry_key(e))
            if cell and e.get("after") is not None:
                found[cell] = e
    return found


def do_read(req: dict) -> dict:
    addr = cell_addr(req)
    if not addr:
        return {"ok": False, "error": "date_not_found_or_missing_target"}
    col, row = addr
    sheet = req["sheet"]
    script = f'''
tell application "Microsoft Excel"
    set theCell to cell ("{col}{row}") of sheet "{sheet}" of workbook "{WORKBOOK}"
    return ((value of theCell) as string) & (character id 31) & (formula of theCell)
end tell
'''
    rc, out, err = osascript(script)
    if rc != 0:
        return {"ok": False, "error": err}
    # US (0x1f) separator (2026-10-19): a "|" inside prose used to truncate
    # the value and leak the rest into `formula`.
    value, formula = (out.split("\x1f", 1) + [""])[:2]
    return {"ok": True, "row": row, "col": col, "value": value, "formula": formula}


_RANGE_RE = re.compile(r"^([A-Z]{1,3})([0-9]{1,5}):([A-Z]{1,3})([0-9]{1,5})$")
MAX_RANGE_CELLS = 20000


def _col_num(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def do_read_range(req: dict) -> dict:
    """Values of a rectangular range in ONE Apple Event (2026-10-30, the 1hcb
    report: ~100 cells as single /read calls cost a round trip each). Rows
    come back as lists of strings; an empty cell is "" and AppleScript renders
    reals in its own text form (1.2345E+4), so callers parse numbers."""
    sheet, rng = req.get("sheet"), str(req.get("range") or "").upper()
    m = _RANGE_RE.match(rng)
    if not sheet or not m:
        return {"ok": False, "error": "missing_sheet_or_bad_range"}
    c1, r1, c2, r2 = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
    if r2 < r1 or _col_num(c2) < _col_num(c1):
        return {"ok": False, "error": "bad_range"}
    if (r2 - r1 + 1) * (_col_num(c2) - _col_num(c1) + 1) > MAX_RANGE_CELLS:
        return {"ok": False, "error": "range_too_large"}
    # Unit (0x1f) between cells, record (0x1e) between rows, as in do_read.
    script = f'''
tell application "Microsoft Excel"
    set v to value of range ("{rng}") of sheet "{safe_str(sheet)}" of workbook "{WORKBOOK}"
end tell
if class of v is not list then set v to {{{{v}}}}
set out to {{}}
set AppleScript's text item delimiters to (character id 31)
repeat with r in v
    set end of out to ((contents of r) as text)
end repeat
set AppleScript's text item delimiters to (character id 30)
return out as text
'''
    rc, out, err = osascript(script)
    if rc != 0:
        return {"ok": False, "error": err}
    rows = [[("" if c == "missing value" else c) for c in ln.split("\x1f")]
            for ln in out.split("\x1e")]
    return {"ok": True, "range": rng, "row": r1, "rows": rows}


def do_lookup(req: dict) -> dict:
    sheet = req.get("sheet")
    date_str = req.get("date")
    if not sheet or not date_str:
        return {"ok": False, "error": "missing_sheet_or_date"}
    r = lookup_row_cached(sheet, date_str)
    return {"ok": True, "row": r} if r else {"ok": False, "error": "date_not_found"}


ROUTES = {
    "/append": do_append,
    "/write":  do_write,
    "/read":   do_read,
    "/read_range": do_read_range,
    "/lookup": do_lookup,
    "/batch":  do_batch,
    "/batch_write": do_batch_write,
    "/ack":    do_ack,
    "/reconcile": do_reconcile,
    "/observe": do_observe,
}
NO_EXCEL = {"/observe"}  # handlers that never touch Excel: skip EXCEL_LOCK


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
            return self._send(200, {"ok": True, "version": VERSION, "macro": MACRO_STATE,
                                    "ledger": LEDGER_DB_STATE})
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
            if self.path in NO_EXCEL:
                result = handler(body)
            else:
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
