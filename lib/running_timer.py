#!/usr/bin/env python3
"""One answer to "which timer is running right now", shared by janus and dtd.

Toggl is the truth, and ``~/.cache/toggl-current.json`` is the single shared
observation of it that every Toggl writer in this repo already write-throughs
(see ``mcp/toggl_server/toggl_api.py``: each live ``get_current()`` persists
here, and any mutation invalidates it). janus polls it; the dtd ticker polls
it; tg-fast and did-fast go through the same module.

Before this, each dtd pane kept its OWN ``/tmp/dtd-<id>.timer`` file and
believed it, so a pane that had not itself started the current task showed a
stale running row while janus had it right (bug 2026-10-19: "dtd is still
showing teams while Janus CLI has it right"). The earlier patch for that
class of bug had janus writing into dtd's own state files, which only papers
over the split brain one writer at a time. Both ends now READ this module
instead ("ideally the same helper function calls both rather than janus
calling dtd"), so a new reader gets the same answer for free.

This module never touches the network. A reader that finds no usable
observation falls back to whatever local state it already had, so nothing
regresses when the cache is cold.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
from pathlib import Path

CURRENT_CACHE = Path.home() / ".cache" / "toggl-current.json"
# Older than this and the reading is ignored. Nothing refreshes the cache
# while janus and every dtd ticker are closed, and a long-dead reading must
# never drive the UI -- better to fall back to the reader's own state.
MAX_AGE_S = 600.0

_ANNOT_RE = re.compile(r" *\(\d*\)| *\[\d*\]| *\{\d*\}| *\[[0-9.+]*/m\]")


def _queue_path() -> Path:
    state = Path(os.environ.get("XDG_STATE_HOME")
                 or (Path.home() / ".local" / "state")) / "jm"
    return state / "task-queue.json"


def norm(s: str) -> str:
    """Annotation-stripped, dash/whitespace-folded, lowercased task name --
    the key a Toggl timer description and a Todoist task content are compared
    on (mirrors did-fast's _norm and janus's _norm_key)."""
    return re.sub(r"[\s\-—–]+", " ", _ANNOT_RE.sub("", s or "")).strip().lower()


def task_for_desc(desc, queue_path=None):
    """The dtd/Todoist task whose content matches this timer description, from
    the task-queue cache. None when nothing matches (a timer that names no
    task is a perfectly ordinary thing)."""
    target = norm(desc)
    if not target:
        return None
    try:
        data = json.loads(Path(queue_path or _queue_path()).read_text())
    except Exception:  # noqa: BLE001 -- no cache is just "no match"
        return None
    # Exact (annotation-stripped, lowercased) name first, then norm()'s
    # dash-folded key. norm() strips a leading dash, so '-1g' == '1g' and the
    # walk returned whichever came first: the -1g ritual timer resolved to the
    # weekly 1g card, and dtd's running row lost its spinner (2026-10-22).
    exact = _ANNOT_RE.sub("", desc or "").strip().lower()
    found = None

    def walk(o, key):
        nonlocal found
        if found is not None:
            return
        if isinstance(o, dict):
            if o.get("id") and o.get("content") and key(o["content"]):
                found = o
                return
            for v in o.values():
                walk(v, key)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)

    walk(data, lambda c: _ANNOT_RE.sub("", c).strip().lower() == exact)
    if found is None:
        walk(data, lambda c: norm(c) == target)
    return found


def _epoch(iso):
    """Toggl's ISO start -> epoch seconds. 0 when unparseable. Python 3.9's
    fromisoformat rejects a trailing Z, which the API does sometimes send."""
    if not iso:
        return 0
    try:
        return int(_dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return 0


def observed(now=None, max_age=MAX_AGE_S, cache=None, queue_path=None):
    """The newest shared reading of Toggl, or None when there is no usable one
    (missing, unreadable, or older than ``max_age``).

    ``{'ts': when Toggl was observed, 'desc': running description,
    'started': its start epoch, 'task_id': the dtd task it names}``. An empty
    desc with ts set means the observation says *nothing* is running, which is
    exactly as useful to a caller as a running entry: it is what lets a stale
    local timer row be cleared.
    """
    now = time.time() if now is None else now
    try:
        raw = json.loads(Path(cache or CURRENT_CACHE).read_text())
    except Exception:  # noqa: BLE001 -- cold cache: the caller keeps its own state
        return None
    try:
        ts = float(raw.get("ts") or 0)
    except (TypeError, ValueError):
        return None
    if not ts or now - ts > max_age:
        return None
    entry = raw.get("entry") or None
    if not isinstance(entry, dict):
        return {"ts": ts, "desc": "", "started": 0, "task_id": ""}
    desc = (entry.get("description") or "").strip()
    task = task_for_desc(desc, queue_path) if desc else None
    return {"ts": ts, "desc": desc, "started": _epoch(entry.get("start")),
            "task_id": str((task or {}).get("id", "") or "")}
