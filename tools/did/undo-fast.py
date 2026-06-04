#!/usr/bin/env python3
"""undo-fast.py — ctrl-z undo for dtd (done / split / defer).

Maintains a session-scoped undo journal (JSONL, LIFO) and reverses the most
recent action:

  done   — restore 0n/1n+ pre-image cell values, strip 0分/hcbi formula
           appends, reopen the Todoist task (recurring: restore prior due),
           delete posthocs, remove from completed-today.json, restart a
           stopped Toggl timer.
  split  — delete the posthoc, restore the original task's content + due,
           reverse the embedded did-fast points log.
  defer  — reschedule the task back, delete the posthoc stub.

Formula appends are reversed by stripping the exact trailing "+N" term when
it is still the tail of the formula, else appending the negation ("-N") —
numerically correct even when later writes interleaved. Cell-value writes
(0n, non-variable 1n+) are restored from pre-images captured by did-fast's
write scripts.

Usage:
    did-fast.py output | undo-fast.py --journal-done <journal>
    defer-fast.py output | undo-fast.py --journal-defer <journal> <name>
    record json         | undo-fast.py --append <journal>
    undo-fast.py --undo <journal> [--session F] [--removed F] [--done-json F]
"""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

TODOIST_TOKEN = "7eb82f47aba8b334769351368e4e3e3284f980e5"
TODOIST_BASE = "https://api.todoist.com/api/v1"
WORKBOOK = "Neon分v12.2.xlsx"
TOGGL_CLI = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"
TG_FAST = Path.home() / "i446-monorepo/tools/tg/tg-fast.py"

# Import ix_osa (AppleScript-over-ssh transport, same pattern as did-fast)
_IX_PATH = Path.home() / ".claude/skills/_lib/ix-osa.py"
_IX_SPEC = importlib.util.spec_from_file_location("ix_osa", _IX_PATH)
_ix_mod = importlib.util.module_from_spec(_IX_SPEC)
sys.modules.setdefault("ix_osa", _ix_mod)
_IX_SPEC.loader.exec_module(_ix_mod)  # type: ignore[union-attr]
ix_run = _ix_mod.run

# Import mark-completed for remove_names / _dup_key
_MC_PATH = Path(__file__).parent / "mark-completed.py"
_MC_SPEC = importlib.util.spec_from_file_location("mark_completed", _MC_PATH)
mc = importlib.util.module_from_spec(_MC_SPEC)
_MC_SPEC.loader.exec_module(mc)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Todoist helpers
# ---------------------------------------------------------------------------

def _api(method: str, path: str, body: dict | None = None,
         timeout: float = 15.0):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{TODOIST_BASE}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


# ---------------------------------------------------------------------------
# AppleScript builders
# ---------------------------------------------------------------------------

def _as_str(s: str) -> str:
    """Escape a Python string into an AppleScript double-quoted literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _as_value(prev: str) -> str:
    """Pre-image text → AppleScript value literal (number, string, or empty)."""
    prev = prev.strip()
    if prev == "" or prev == "missing value":
        return '""'
    if _NUM_RE.match(prev):
        return prev
    return _as_str(prev)


def build_0n_restore_script(restores: list[tuple[int, str]], target_md: str) -> str:
    """Restore 0n cells to their pre-write values. restores = [(col_num, prev_text)]."""
    parts = target_md.split("/")
    month, day = parts[0], parts[1]
    set_lines = [
        f"    set value of cell {col} of row todayRow of ws to {_as_value(prev)}"
        for col, prev in restores
    ]
    return f'''tell application "Microsoft Excel"
    set ws to sheet "0n" of workbook "{WORKBOOK}"
    set todayRow to 0
    repeat with r from 3 to 500
        set cellDate to value of cell 3 of row r of ws
        if cellDate is not missing value then
            try
                set m to (month of (cellDate as date)) as integer
                set d to day of (cellDate as date)
                if m = {month} and d = {day} then
                    set todayRow to r
                    exit repeat
                end if
            end try
        end if
    end repeat
    if todayRow = 0 then return "ERROR: date {target_md} not found"
{chr(10).join(set_lines)}
    return "OK:row=" & todayRow
end tell'''


def _strip_or_negate_lines(cell_expr: str, term: str, suffix: str) -> str:
    """AppleScript that removes a '+N' (or '+'1n+'!X12') term from a formula:
    strips it if it is the exact tail, else appends the negation."""
    neg = "-" + term[1:]
    tlen = len(term)
    return f'''    set theCell{suffix} to {cell_expr}
    set f{suffix} to ""
    try
        set f{suffix} to (formula of theCell{suffix}) as text
    end try
    if f{suffix} ends with {_as_str(term)} and (length of f{suffix}) > {tlen} then
        set formula of theCell{suffix} to text 1 thru ((length of f{suffix}) - {tlen}) of f{suffix}
    else if f{suffix} is not "" then
        set formula of theCell{suffix} to f{suffix} & {_as_str(neg)}
    end if'''


def build_fen_strip_script(strips: list[tuple[str, str]], target_md: str,
                           sheet: str = "0分", max_row: int = 200) -> str:
    """Strip/negate formula terms on a date-keyed sheet (0分 or hcbi).
    strips = [(col_letter, term)] where term is the exact appended text."""
    op_lines = [
        _strip_or_negate_lines(f'range ("{col}" & todayRow) of ws', term, str(i))
        for i, (col, term) in enumerate(strips)
    ]
    return f'''tell application "Microsoft Excel"
    set ws to sheet "{sheet}" of workbook "{WORKBOOK}"
    set todayRow to 0
    repeat with i from 2 to {max_row}
        if (string value of range ("B" & i) of ws) = "{target_md}" then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow = 0 then return "ERROR: date {target_md} not found in {sheet}"
{chr(10).join(op_lines)}
    return "OK:{sheet} row=" & todayRow
end tell'''


def build_1n_undo_script(restores: list[tuple[str, str, str]],
                         strips: list[tuple[str, str, str]]) -> str:
    """Undo 1n+ writes. restores = [(row, col_letter, prev_formula)] (pre-image
    restore for non-variable writes); strips = [(row, col_letter, term)]
    (strip/negate for variable appends)."""
    lines = []
    for i, (row, col, prev) in enumerate(restores):
        prev = prev.strip()
        if prev == "" or prev == "missing value":
            lines.append(f'    set value of range ("{col}{row}") of ws1n to ""')
        else:
            lines.append(f'    set formula of range ("{col}{row}") of ws1n to {_as_str(prev)}')
    for i, (row, col, term) in enumerate(strips):
        lines.append(_strip_or_negate_lines(f'range ("{col}{row}") of ws1n', term, f"s{i}"))
    return f'''tell application "Microsoft Excel"
    set ws1n to sheet "1n+" of workbook "{WORKBOOK}"
{chr(10).join(lines)}
    return "OK:1n+"
end tell'''


# ---------------------------------------------------------------------------
# Reversal core
# ---------------------------------------------------------------------------

def _run_excel(script: str, label: str, errors: list[str]) -> None:
    res = ix_run(script, timeout=30.0)
    out = (res.stdout or "").strip()
    if res.returncode != 0 or out.startswith("ERROR"):
        errors.append(f"{label}: {out or (res.stderr or '').strip() or 'failed'}")


def _reverse_todoist_entry(td: dict, today_iso: str, errors: list[str]) -> None:
    tid = td.get("id")
    if not tid:
        return
    try:
        if td.get("deferred"):
            # did-fast --defer path: restore original content + due date
            _api("POST", f"/tasks/{tid}", {
                "content": td.get("content", ""),
                "due_date": td.get("prev_due") or today_iso,
            })
        elif td.get("closed"):
            if td.get("recurring"):
                # Recurring close advanced the due date; restore it.
                _api("POST", f"/tasks/{tid}", {
                    "due_date": td.get("prev_due") or today_iso,
                })
            else:
                _api("POST", f"/tasks/{tid}/reopen")
    except Exception as e:
        errors.append(f"todoist {tid}: {e}")


def reverse_didfast_output(out: dict, target_md: str, today_iso: str,
                           errors: list[str]) -> None:
    """Reverse all side effects recorded in one did-fast output JSON."""
    results = out.get("results", [])

    on_restores: list[tuple[int, str]] = []
    fen_strips: list[tuple[str, str]] = []
    hcbi_strips: list[tuple[str, str]] = []
    n1_restores: list[tuple[str, str, str]] = []
    n1_strips: list[tuple[str, str, str]] = []

    for e in results:
        step = e.get("step")
        undo = e.get("undo") or {}

        if step == "0n" and e.get("col") is not None and "prev_0n" in undo:
            on_restores.append((e["col"], undo["prev_0n"]))

        fen = e.get("0fen")
        if (fen and fen.get("points", 0) > 0
                and not (step == "1n" and not e.get("variable_1n"))):
            fen_strips.append((fen["col"], f"+{fen['points']}"))

        if e.get("curly_q"):
            fen_strips.append(("Q", f"+{e['curly_q']}"))

        if e.get("hcbi"):
            hcbi_strips.append((e["hcbi"]["col"], f"+{e['hcbi']['mins']}"))

        if step == "1n" and e.get("col_letter") and e.get("week_row"):
            row, col = str(e["week_row"]), e["col_letter"]
            if e.get("variable_1n") and e.get("variable_value"):
                n1_strips.append((row, col, f"+{e['variable_value']}"))
            else:
                if "prev_1n_formula" in undo:
                    n1_restores.append((row, col, undo["prev_1n_formula"]))
                if e.get("fen_col"):
                    fen_strips.append((e["fen_col"], f"+'1n+'!{col}{row}"))

        td = e.get("todoist")
        if td:
            _reverse_todoist_entry(td, today_iso, errors)

        ph = e.get("posthoc")
        if ph and ph.get("id"):
            try:
                _api("DELETE", f"/tasks/{ph['id']}")
            except Exception as ex:
                errors.append(f"posthoc delete {ph['id']}: {ex}")

    if on_restores:
        _run_excel(build_0n_restore_script(on_restores, target_md), "0n", errors)
    if fen_strips:
        _run_excel(build_fen_strip_script(fen_strips, target_md, "0分", 200), "0分", errors)
    if hcbi_strips:
        _run_excel(build_fen_strip_script(hcbi_strips, target_md, "hcbi", 500), "hcbi", errors)
    if n1_restores or n1_strips:
        _run_excel(build_1n_undo_script(n1_restores, n1_strips), "1n+", errors)

    # Restart a Toggl timer that the done action stopped (best-effort)
    ts = out.get("toggl_stopped")
    if ts and ts.get("stopped") and ts.get("description"):
        try:
            desc = ts["description"]
            proj = subprocess.run(
                ["python3", str(TG_FAST), "--resolve", desc],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            cmd = ["python3", str(TOGGL_CLI), "start", desc]
            if proj:
                cmd.append(proj)
            subprocess.run(cmd, capture_output=True, timeout=10)
        except Exception:
            pass  # non-critical


def reverse_record(record: dict, errors: list[str]) -> None:
    today = date.today()
    today_iso = today.isoformat()
    target_md = f"{today.month}/{today.day}"
    rtype = record.get("type")

    if rtype == "done":
        reverse_didfast_output(record.get("output") or {}, target_md, today_iso, errors)
        mc.remove_names(record.get("names", []))

    elif rtype == "defer":
        tid = record.get("task_id")
        if tid:
            try:
                _api("POST", f"/tasks/{tid}", {
                    "due_date": record.get("prev_due") or today_iso,
                })
            except Exception as e:
                errors.append(f"reschedule {tid}: {e}")
        stub = record.get("stub_id")
        if stub:
            try:
                _api("DELETE", f"/tasks/{stub}")
            except Exception as e:
                errors.append(f"stub delete {stub}: {e}")

    elif rtype == "split":
        ph = record.get("posthoc_id")
        if ph:
            try:
                _api("DELETE", f"/tasks/{ph}")
            except Exception as e:
                errors.append(f"posthoc delete {ph}: {e}")
        tid = record.get("task_id")
        if tid:
            try:
                _api("POST", f"/tasks/{tid}", {
                    "content": record.get("prev_content", ""),
                    "due_date": record.get("prev_due") or today_iso,
                })
            except Exception as e:
                errors.append(f"restore task {tid}: {e}")
        didfast = record.get("didfast")
        if didfast:
            reverse_didfast_output(didfast, target_md, today_iso, errors)
        mc.remove_names(record.get("names", []))

    else:
        errors.append(f"unknown record type: {rtype}")


# ---------------------------------------------------------------------------
# Filter-file cleanup (so the task reappears in the running dtd list)
# ---------------------------------------------------------------------------

def _dup_key(name: str) -> str:
    return mc._dup_key(name)


def clean_filter_files(names: list[str], session: str | None,
                       removed: str | None, done_json: str | None) -> None:
    keys = {_dup_key(n) for n in names if _dup_key(n)}
    if not keys:
        return

    for fpath in (session, removed):
        if not fpath or not os.path.exists(fpath):
            continue
        try:
            with open(fpath) as f:
                lines = f.readlines()
            kept = [l for l in lines if _dup_key(l.strip()) not in keys]
            tmp = fpath + ".tmp"
            with open(tmp, "w") as f:
                f.writelines(kept)
            os.replace(tmp, fpath)
        except OSError:
            pass

    if done_json and os.path.exists(done_json):
        try:
            with open(done_json) as f:
                arr = json.load(f)
            if isinstance(arr, list):
                arr = [n for n in arr if _dup_key(str(n)) not in keys]
                tmp = done_json + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(arr, f, ensure_ascii=False)
                os.replace(tmp, done_json)
        except (OSError, json.JSONDecodeError):
            pass


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

def _stamp(record: dict) -> dict:
    record.setdefault("date", date.today().isoformat())
    record.setdefault("ts", datetime.now().strftime("%H:%M:%S"))
    return record


def journal_append(journal: str, record: dict) -> None:
    fd = os.open(journal, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, (json.dumps(_stamp(record), ensure_ascii=False) + "\n").encode())
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def journal_pop_and_reverse(journal: str, session: str | None,
                            removed: str | None, done_json: str | None) -> dict:
    """Pop the last journal record and reverse it. Holds flock for the whole
    pop+reverse so rapid repeated ctrl-z serializes into clean LIFO undos."""
    if not os.path.exists(journal):
        return {"error": "nothing to undo"}

    fd = os.open(journal, os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(os.dup(fd), "r+") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
            if not lines:
                return {"error": "nothing to undo"}
            try:
                record = json.loads(lines[-1])
            except json.JSONDecodeError:
                # Drop the corrupt line so the next ctrl-z reaches a valid one
                f.seek(0)
                f.truncate()
                f.write("".join(l + "\n" for l in lines[:-1]))
                return {"error": "corrupt journal record dropped — retry"}

            if record.get("date") != date.today().isoformat():
                return {"error": f"stale record from {record.get('date')} — not undoing"}

            # Pop first (consistent LIFO even if reversal partially fails)
            f.seek(0)
            f.truncate()
            f.write("".join(l + "\n" for l in lines[:-1]))
            f.flush()

            errors: list[str] = []
            reverse_record(record, errors)
            names = record.get("names", [])
            clean_filter_files(names, session, removed, done_json)

            out = {
                "ok": True,
                "type": record.get("type"),
                "names": names,
                "summary": f"undid {record.get('type')}: {', '.join(names) or '?'}",
            }
            if errors:
                out["errors"] = errors
                out["summary"] += f" ({len(errors)} issue{'s' if len(errors) > 1 else ''})"
            return out
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2

    mode = args[0]

    if mode == "--journal-done":
        if len(args) < 2:
            print("usage: undo-fast.py --journal-done <journal>", file=sys.stderr)
            return 2
        try:
            out = json.loads(sys.stdin.read())
        except json.JSONDecodeError:
            return 0  # did-fast errored; nothing to journal
        results = out.get("results") or []
        if not results:
            return 0
        journal_append(args[1], {
            "type": "done",
            "names": [r.get("name", "") for r in results if r.get("name")],
            "output": out,
        })
        return 0

    if mode == "--journal-defer":
        if len(args) < 3:
            print("usage: undo-fast.py --journal-defer <journal> <name>", file=sys.stderr)
            return 2
        try:
            out = json.loads(sys.stdin.read())
        except json.JSONDecodeError:
            return 0
        if not out.get("task_id"):
            return 0
        journal_append(args[1], {
            "type": "defer",
            "names": [args[2]],
            "task_id": out["task_id"],
            "prev_due": out.get("prev_due", ""),
            "prev_due_string": out.get("prev_due_string", ""),
            "recurring": out.get("recurring", False),
            "target_date": out.get("target_date", ""),
            "stub_id": (out.get("stubs") or {}).get("today"),
        })
        return 0

    if mode == "--append":
        if len(args) < 2:
            print("usage: undo-fast.py --append <journal>", file=sys.stderr)
            return 2
        try:
            record = json.loads(sys.stdin.read())
        except json.JSONDecodeError:
            return 0
        journal_append(args[1], record)
        return 0

    if mode == "--undo":
        if len(args) < 2:
            print("usage: undo-fast.py --undo <journal> [--session F] [--removed F] [--done-json F]",
                  file=sys.stderr)
            return 2
        journal = args[1]
        opts = {"--session": None, "--removed": None, "--done-json": None}
        i = 2
        while i + 1 < len(args) + 1 and i < len(args):
            if args[i] in opts and i + 1 < len(args):
                opts[args[i]] = args[i + 1]
                i += 2
            else:
                i += 1
        result = journal_pop_and_reverse(
            journal, opts["--session"], opts["--removed"], opts["--done-json"])
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok") else 1

    print(f"unknown mode: {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
