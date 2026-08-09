#!/usr/bin/env python3
"""undo-fast.py — ctrl-z undo for dtd (done / split / defer / delete).

Maintains a session-scoped undo journal (JSONL, LIFO) and reverses the most
recent action:

  done   — restore 0n/1n+ pre-image cell values, strip 0分/hcbi formula
           appends, reopen the Todoist task (recurring: restore prior due),
           delete posthocs, remove from completed-today.json, restart a
           stopped Toggl timer.
  split  — delete the posthoc, restore the original task's content + due,
           reverse the embedded did-fast points log.
  defer  — reschedule the task back, delete the posthoc stub.
  delete — recreate the task from the pre-image journaled by dtd's ctrl-x
           (content, project, section, labels, priority, due, duration).

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

# Strip "starting <date>"-style anchors off a recurrence string (same regex
# as defer-fast.py's _ANCHOR_RE) so restoring a cadence doesn't re-anchor it.
_ANCHOR_RE = re.compile(
    r"\s+(starting|start|from|beginning|begins?|since)\b.*$", re.I)
WORKBOOK = "Neon分v12.2.xlsx"
TOGGL_CLI = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"
TG_FAST = Path.home() / "i446-monorepo/tools/tg/tg-fast.py"

# excel-http client — journaled 0分/hcbi strip writes (curls localhost on ix,
# ssh+osascript fallback built in)
sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
from neon import excel
import neon_blocks as nb

BUILD_ORDER = Path.home() / "vault/g245/5e-1/build-order.md"

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


def _strip_or_negate(formula: str, term: str) -> str | None:
    """Python port of _strip_or_negate_lines: remove a '+N' (or '+'1n+'!X12')
    term from a formula — strip it if it is the exact tail, else append the
    negation. Returns the new formula, or None when the cell is empty
    (nothing to reverse)."""
    neg = "-" + term[1:]
    f = formula or ""
    if f.endswith(term) and len(f) > len(term):
        return f[: -len(term)]
    if f != "":
        return f + neg
    return None


def strip_fen_terms(strips: list[tuple[str, str]], target_md: str,
                    sheet: str, errors: list[str], src: str) -> None:
    """Strip/negate formula terms on a date-keyed sheet (0分 or hcbi) via the
    excel-http client: read the current formula, strip/negate in Python,
    write back (journaled). strips = [(col_letter, term)] where term is the
    exact appended text."""
    row = None
    for col, term in strips:
        try:
            r = (excel.read(sheet, col, row=row) if row
                 else excel.read(sheet, col, date=target_md))
        except Exception as e:
            errors.append(f"{sheet} {col}: read failed: {e}")
            continue
        if not r.get("ok"):
            errors.append(f"{sheet} {col}: read failed: {r.get('error') or '?'}")
            continue
        row = r.get("row") or row
        new_formula = _strip_or_negate(r.get("formula") or "", term)
        if new_formula is None:
            continue
        try:
            w = excel.write(sheet, col, row=row, value=new_formula, src=src)
        except Exception as e:
            errors.append(f"{sheet} {col}: write failed: {e}")
            continue
        if not w.get("ok"):
            errors.append(f"{sheet} {col}: write failed: {w.get('error') or '?'}")


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


def _on_ix() -> bool:
    """True when this process runs on Ix — build-order.md's single writer
    (same check as did-fast.py's _on_ix; avoids an ssh-to-self hop that can
    wedge on a stale Tailscale MagicSock)."""
    import socket
    return "mac-mini" in socket.gethostname().lower()


def _unstamp_ritual(block: str, emoji: str) -> dict | None:
    """Remove `emoji` from `block`'s header on the single-writer copy
    (Ix), then recompute the day's -1₦ formula from the result. Mirrors
    did-fast.py's _stamp_on_ix: same lock file, same flock-serialized
    read-modify-write, just the inverse edit. Returns
    {"changed": bool, "formula": str} or None on ssh failure."""
    py = (
        "import sys, fcntl, json; sys.path.insert(0, '/Users/mckay/i446-monorepo/lib')\n"
        "import neon_blocks as nb\n"
        "from pathlib import Path\n"
        "bo = Path.home() / 'vault/g245/5e-1/build-order.md'\n"
        "lock_path = bo.with_suffix('.lock')\n"
        "with open(lock_path, 'a') as lf:\n"
        "    fcntl.flock(lf.fileno(), fcntl.LOCK_EX)\n"
        "    try:\n"
        "        t = bo.read_text(encoding='utf-8')\n"
        f"        nt, ch = nb.unstamp_emoji(t, {block!r}, {emoji!r})\n"
        "        if ch: bo.write_text(nt, encoding='utf-8')\n"
        "        _, _, formula = nb.score_day(nt)\n"
        "    finally:\n"
        "        fcntl.flock(lf.fileno(), fcntl.LOCK_UN)\n"
        "print(json.dumps({'changed': ch, 'formula': formula}))\n"
    )
    if _on_ix():
        try:
            r = subprocess.run(["python3", "-c", py],
                               capture_output=True, text=True, timeout=15)
        except Exception:
            return None
    else:
        try:
            r = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                 "ix", "python3", "-"],
                input=py, capture_output=True, text=True, timeout=15)
        except Exception:
            return None
    if r.returncode != 0:
        return None
    lines = (r.stdout or "").strip().splitlines()
    try:
        return json.loads(lines[-1]) if lines else None
    except Exception:
        return None


def _reverse_ritual_entry(e: dict, today_iso: str, target_md: str, errors: list[str]) -> None:
    """Undo a ritual completion: reopen the Todoist card, un-stamp the header
    emoji, and recompute+SET 0分!P from the corrected header — the inverse of
    run_ritual's own (close, stamp, credit) sequence.

    The top-level entry["todoist"] (handled by the generic loop below)
    deliberately omits the task id (did-fast.py, see the comment above
    ritual_entries.append) so the generic _reverse_todoist_entry() never
    half-undoes a ritual card — reopening it without also un-stamping the
    header and reversing the point credit would leave the header/P out of
    sync with an open card. The full state this function needs lives in
    entry["ritual"], run_ritual's own return value, journaled verbatim."""
    ritual = e.get("ritual") or {}
    rtd = ritual.get("todoist") or {}
    if rtd.get("id"):
        _reverse_todoist_entry(rtd, today_iso, errors)
    if not ritual.get("stamped"):
        return  # this completion didn't change the header — nothing to unstamp
    block, emoji = ritual.get("block"), ritual.get("emoji")
    if not (block and emoji):
        return
    result = _unstamp_ritual(block, emoji)
    if result is None:
        errors.append(f"ritual {block} {emoji}: unstamp failed (ix unreachable)")
        return
    if not result.get("changed"):
        return
    credited = bool((ritual.get("p_credit") or {}).get("ok"))
    if credited:
        w = excel.write("0分", "P", date=target_md, value=result["formula"],
                        src=f"undo ritual {block} {ritual.get('ritual', '?')}")
        if not w.get("ok"):
            errors.append(f"0分 P: undo write failed: {w.get('error') or '?'}")


def reverse_didfast_output(out: dict, target_md: str, today_iso: str,
                           errors: list[str]) -> None:
    """Reverse all side effects recorded in one did-fast output JSON."""
    results = out.get("results", [])

    # Per-script success flags: don't strip/negate a term whose original
    # append never landed (that would subtract points never added). A
    # missing *_write key means the write wasn't attempted for that batch;
    # entries gate on their own data presence, so default True is safe.
    fen_ok = (out.get("0fen_write") or {}).get("ok", True)
    hcbi_ok = (out.get("hcbi_write") or {}).get("ok", True)
    n1_ok = (out.get("1n_write") or {}).get("ok", True)
    n1fen_ok = (out.get("1n_0fen_write") or {}).get("ok", True)

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
        if (fen_ok and fen and fen.get("points", 0) > 0
                and not (step == "1n" and not e.get("variable_1n"))):
            fen_strips.append((fen["col"], f"+{fen['points']}"))

        if fen_ok and e.get("curly_q"):
            fen_strips.append(("Q", f"+{e['curly_q']}"))

        if hcbi_ok and e.get("hcbi"):
            hcbi_strips.append((e["hcbi"]["col"], f"+{e['hcbi']['mins']}"))

        if step == "1n" and e.get("col_letter") and e.get("week_row"):
            row, col = str(e["week_row"]), e["col_letter"]
            if e.get("variable_1n") and e.get("variable_value"):
                if n1_ok:
                    n1_strips.append((row, col, f"+{e['variable_value']}"))
            else:
                if "prev_1n_formula" in undo:
                    n1_restores.append((row, col, undo["prev_1n_formula"]))
                if n1fen_ok and e.get("fen_col"):
                    fen_strips.append((e["fen_col"], f"+'1n+'!{col}{row}"))

        if step == "ritual":
            _reverse_ritual_entry(e, today_iso, target_md, errors)
            continue  # its own todoist/header/P handling above is exhaustive

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
    undo_names = [e.get("name") for e in results if e.get("name")]
    undo_src = "undo " + (", ".join(undo_names) if undo_names else "?")
    if fen_strips:
        strip_fen_terms(fen_strips, target_md, "0分", errors, undo_src)
    if hcbi_strips:
        strip_fen_terms(hcbi_strips, target_md, "hcbi", errors, undo_src)
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
            # Unhide from today's deferred-habit marker FIRST (defer-fast's
            # mark_habit_deferred) — even if the API reschedule below fails,
            # the card should reappear in dtd rather than stay hidden all day.
            _unmark_habit_deferred(tid)
            body = {"due_date": record.get("prev_due") or today_iso}
            # Recurring parent: a bare due_date write silently strips the
            # recurrence, so restore the cadence too (anchor stripped — the
            # old "starting <date>" would fight the due_date).
            if record.get("recurring") and record.get("prev_due_string"):
                pattern = _ANCHOR_RE.sub(
                    "", record["prev_due_string"]).strip()
                if pattern:
                    body["due_string"] = pattern
            try:
                _api("POST", f"/tasks/{tid}", body)
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

    elif rtype == "delete":
        # Recreate from the pre-image captured before the hard DELETE. The
        # new task gets a fresh id; later dtd actions resolve by content so
        # only a repeat ctrl-x on the stale cached id would miss.
        t = record.get("task") or {}
        body = {"content": t.get("content")
                or (record.get("names") or ["?"])[0]}
        for k in ("description", "priority", "labels", "project_id",
                  "section_id", "parent_id"):
            v = t.get(k)
            if v:
                body[k] = v
        due = t.get("due") or {}
        if due.get("is_recurring") and due.get("string"):
            body["due_string"] = due["string"]
        elif due.get("datetime"):
            body["due_datetime"] = due["datetime"]
        elif due.get("date"):
            body["due_date"] = str(due["date"])[:10]
        dur = t.get("duration") or {}
        if isinstance(dur, dict) and dur.get("amount"):
            body["duration"] = dur["amount"]
            body["duration_unit"] = dur.get("unit", "minute")
        try:
            _api("POST", "/tasks", body)
        except Exception as e:
            errors.append(f"recreate task: {e}")

    else:
        errors.append(f"unknown record type: {rtype}")


# ---------------------------------------------------------------------------
# Filter-file cleanup (so the task reappears in the running dtd list)
# ---------------------------------------------------------------------------

def _dup_key(name: str) -> str:
    return mc._dup_key(name)


def _unmark_habit_deferred(task_id: str) -> None:
    """Remove a parent id from today's habits-deferred marker (written by
    defer-fast.mark_habit_deferred; read by dtd's list builder). Best-effort:
    a missing file or lost race just means the card stays hidden until the
    next day's marker file takes over."""
    from datetime import date as _date
    p = os.path.expanduser(f"~/.cache/jm/habits-deferred-{_date.today().isoformat()}.ids")
    if not os.path.exists(p):
        return
    try:
        with open(p) as f:
            lines = f.readlines()
        kept = [l for l in lines if l.strip() != str(task_id)]
        if len(kept) != len(lines):
            tmp = p + ".tmp"
            with open(tmp, "w") as f:
                f.writelines(kept)
            os.replace(tmp, p)
    except OSError:
        pass


def clean_filter_files(names: list[str], session: str | None,
                       removed: str | None, done_json: str | None,
                       task_id: str | None = None,
                       task_ids: list[str] | None = None) -> None:
    keys = {_dup_key(n) for n in names if _dup_key(n)}
    if keys:
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

    # id-keyed hide (dtd's $REMOVED.ids — completions/defers hide by id, not
    # name, so two same-named tasks can't suppress each other; see dtd.sh's
    # enter/done/defer bindings). Strip ONLY these tasks' ids, never by name,
    # for the same collision-safety reason the hide itself exists.
    strip_ids = {str(i) for i in (task_ids or [])}
    if task_id:
        strip_ids.add(str(task_id))
    if strip_ids and removed:
        ids_path = removed + ".ids"
        if os.path.exists(ids_path):
            try:
                with open(ids_path) as f:
                    lines = f.readlines()
                kept = [l for l in lines if l.strip() not in strip_ids]
                tmp = ids_path + ".tmp"
                with open(tmp, "w") as f:
                    f.writelines(kept)
                os.replace(tmp, ids_path)
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
            clean_filter_files(names, session, removed, done_json,
                               task_id=record.get("task_id"),
                               task_ids=record.get("task_ids"))

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
            print("usage: undo-fast.py --journal-done <journal> [task_id]",
                  file=sys.stderr)
            return 2
        try:
            out = json.loads(sys.stdin.read())
        except json.JSONDecodeError:
            return 0  # did-fast errored; nothing to journal
        results = out.get("results") or []
        if not results:
            return 0
        # Completions hide optimistically by id in dtd's $REMOVED.ids
        # (2026-07-24: name-hide suppressed same-named duplicates). Record
        # every id we know — the fzf row id the worker passed, plus any
        # todoist ids did-fast matched — so --undo can strip the hide.
        task_ids = [args[2]] if len(args) > 2 and args[2] else []
        for r in results:
            tid = (r.get("todoist") or {}).get("id")
            if tid and str(tid) not in task_ids:
                task_ids.append(str(tid))
        journal_append(args[1], {
            "type": "done",
            "names": [r.get("name", "") for r in results if r.get("name")],
            "task_ids": task_ids,
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
