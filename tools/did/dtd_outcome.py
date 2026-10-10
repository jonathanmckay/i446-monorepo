#!/usr/bin/env python3
"""dtd_outcome.py: classify a did-fast run per requested item, for dtd's worker.

    did-fast stdout | dtd_outcome.py <journal> <rc> <id>\t<content> [...]

One line per requested item on stdout: "<id>\t<status>\t<display>", status one of
  ok       did-fast returned a results entry for it
  already  did-fast skipped it as already done today (future_skipped)
  recorded no entry in the output, but completed-today.json has it: the
           completion landed and only the report was lost
  agent    did-fast needs a human/agent (display carries the reason)
  unknown  none of the above; the item really did not complete

Why it exists (2026-10-10): the worker decided success with one jq over the
output and treated anything else as "? restored to list". 15 of 54 completions
in one session got that verdict although every one of them was recorded in
completed-today.json, so the failure signal had become noise.

Also (batch path) journals each item's slice of a batched did-fast output as
its own ctrl-z entry, so undo still reverses one completion at a time, and
appends every `unknown` to ~/.local/state/jm/dtd-unparsed.jsonl with the raw
output, so the next real failure arrives with its evidence.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "jm"
COMPLETED = STATE / "completed-today.json"
UNPARSED = STATE / "dtd-unparsed.jsonl"
HERE = Path(__file__).resolve().parent

# Top-level did-fast fields undo-fast reads besides results[] (undo-fast.py
# _undo_done: *_write ok flags gate the Excel reversals).
_SHARED = ("0n_write", "0fen_write", "hcbi_write", "1n_write", "1n_0fen_write")


def _norm(s: str) -> str:
    return " ".join(str(s).lower().split())


def parse_output(raw: str) -> dict | None:
    """did-fast's JSON, tolerating stray text around it (pre-2026-10-10
    did-fast let child processes write to stdout)."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    best = None
    i = raw.find("{")
    while i != -1:
        try:
            obj, _end = dec.raw_decode(raw, i)
            if isinstance(obj, dict) and ("results" in obj or "agent_needed" in obj):
                best = obj
        except json.JSONDecodeError:
            pass
        i = raw.find("{", i + 1)
    return best


def _matches(entry_name: str, entry_id, item_id: str, content: str) -> bool:
    if item_id and entry_id and str(entry_id) == item_id:
        return True
    n, c = _norm(entry_name), _norm(content)
    return bool(n) and (c == n or c.startswith(n + " "))


def _completed_today() -> tuple[set[str], set[str]]:
    try:
        ct = json.loads(COMPLETED.read_text())
    except Exception:  # noqa: BLE001
        return set(), set()
    if ct.get("date") != time.strftime("%Y-%m-%d"):
        return set(), set()
    return ({str(v) for v in (ct.get("ids") or {}).values()},
            {_norm(n) for n in ct.get("names") or []})


def classify(out: dict | None, items: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
    results = (out or {}).get("results") or []
    agent = (out or {}).get("agent_needed") or []
    skipped = (out or {}).get("future_skipped") or []
    done_ids, done_names = _completed_today()
    rows = []
    for item_id, content in items:
        hit = next((r for r in results
                    if _matches(r.get("name", ""), (r.get("todoist") or {}).get("id"), item_id, content)), None)
        if hit:
            closed = "✓" if (hit.get("todoist") or {}).get("closed") else ""
            rows.append((item_id, "ok", f"{hit.get('name', content)} → {hit.get('step', '')} {closed}".rstrip()))
            continue
        sk = next((s for s in skipped if _matches(s.get("name", ""), s.get("id"), item_id, content)), None)
        if sk:
            rows.append((item_id, "already", f"{content} (already done today)"))
            continue
        ag = next((a for a in agent if _matches(a.get("name", ""), None, item_id, content)), None)
        if ag:
            rows.append((item_id, "agent", f"{content} — {ag.get('reason', 'needs input')}"))
            continue
        base = _norm(content).rsplit(" ", 1)[0] if _norm(content).rsplit(" ", 1)[-1].isdigit() else _norm(content)
        if (item_id and item_id in done_ids) or _norm(content) in done_names or base in done_names:
            rows.append((item_id, "recorded", f"{content} (recorded)"))
            continue
        rows.append((item_id, "unknown", content))
    return rows


def journal_split(journal: str, out: dict, rows: list[tuple[str, str, str]],
                  items: list[tuple[str, str]]) -> None:
    """One ctrl-z entry per item, each carrying only that item's results."""
    spec = importlib.util.spec_from_file_location("undo_fast_for_outcome", HERE / "undo-fast.py")
    uf = importlib.util.module_from_spec(spec)
    sys.modules["undo_fast_for_outcome"] = uf
    spec.loader.exec_module(uf)
    results = out.get("results") or []
    toggl = out.get("toggl_stopped")
    for (item_id, content), (_id, status, _d) in zip(items, rows):
        if status != "ok":
            continue
        mine = [r for r in results
                if _matches(r.get("name", ""), (r.get("todoist") or {}).get("id"), item_id, content)]
        sub = {"results": mine, "agent_needed": []}
        for k in _SHARED:
            if k in out:
                sub[k] = out[k]
        if toggl:
            sub["toggl_stopped"], toggl = toggl, None   # restart the timer once, with the first undo
        ids = [item_id] if item_id else []
        ids += [str((r.get("todoist") or {}).get("id")) for r in mine
                if (r.get("todoist") or {}).get("id") and str((r.get("todoist") or {}).get("id")) not in ids]
        uf.journal_append(journal, {"type": "done", "names": [r.get("name", "") for r in mine],
                                    "task_ids": ids, "output": sub})


def record_unknown(raw: str, rc: str, rows, items) -> None:
    bad = [(i, c) for (i, c), (_x, s, _d) in zip(items, rows) if s == "unknown"]
    if not bad:
        return
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with UNPARSED.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "rc": rc, "items": bad, "stdout": raw[:4000]},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: did-fast stdout | dtd_outcome.py <journal|-> <rc> <id>\\t<content> ...", file=sys.stderr)
        return 2
    journal, rc = argv[0], argv[1]
    items = [tuple(a.split("\t", 1)) if "\t" in a else ("", a) for a in argv[2:]]
    raw = sys.stdin.read()
    out = parse_output(raw)
    rows = classify(out, items)
    if out and journal != "-" and len(items) > 1:
        try:
            journal_split(journal, out, rows, items)
        except Exception as e:  # noqa: BLE001: never block the worker on undo bookkeeping
            print(f"journal split failed: {e}", file=sys.stderr)
    record_unknown(raw, rc, rows, items)
    for r in rows:
        print("\t".join(r))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
