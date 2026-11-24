"""defer_log.py: how many times each task has been deferred.

defer-fast appends one line per real deferral of a one-off task; undo-fast
appends a tombstone when the defer is undone. group_stall.py reads the totals
on Ix and lists a task deferred DEFER_LIMIT times on the stalled card, so the
fourth move is a decision (re-value, delegate, drop) instead of another date.

Per-host append-only files in the vault inbox, carried by Syncthing, because
defers run wherever dtd runs (Straylight, Ix, the phone via Ix) and the stall
job runs on Ix only. Append-only so two hosts never rewrite the same file.

Not counted, on purpose: a defer to today or earlier ("delay by 0", JM's way
of clearing duplicates), habit and dated habit copies (he batches those), and
reschedules made outside defer-fast (Todoist's own apps, /did --tmrw).
"""
from __future__ import annotations

import json
import re
import socket
from collections import Counter
from datetime import date
from pathlib import Path

LOG_DIR = Path.home() / "vault" / "z_ibx"
DEFER_LIMIT = 3
SKIP_LABELS = frozenset({"0neon", "1neon", "-1neon", "夜neon", "5^2₦", "posthoc"})


def _host() -> str:
    return re.sub(r"[^a-z0-9]+", "-", socket.gethostname().lower()).strip("-")[:24]


def _path(log_dir: Path | None = None) -> Path:
    return (log_dir or LOG_DIR) / f"defer-log-{_host()}.jsonl"


def _append(row: dict, log_dir: Path | None = None) -> bool:
    p = _path(log_dir)
    if not p.parent.is_dir():
        return False
    with open(p, "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True


def counts_as_deferral(labels, target: str, today: date) -> bool:
    """A one-off task moved to a later day. An unparseable target counts: it
    was passed through to Todoist as a date string, so the task did move."""
    if SKIP_LABELS & set(labels or []):
        return False
    try:
        return date.fromisoformat((target or "")[:10]) > today
    except ValueError:
        return bool(target)


def record(task_id, content: str, labels, target: str, today: date,
           log_dir: Path | None = None) -> bool:
    """Log one deferral if it counts. Best-effort: never raises."""
    try:
        if not task_id or not counts_as_deferral(labels, target, today):
            return False
        return _append({"id": str(task_id), "day": today.isoformat(),
                        "target": target, "content": content}, log_dir)
    except OSError:
        return False


def undo(task_id, log_dir: Path | None = None) -> bool:
    """Take back the latest deferral of this task (a tombstone, not a rewrite)."""
    try:
        return bool(task_id) and _append({"id": str(task_id), "undo": True}, log_dir)
    except OSError:
        return False


def counts(log_dir: Path | None = None) -> dict:
    """{task id: deferrals minus undos} across every host's log."""
    n = Counter()
    for p in sorted((log_dir or LOG_DIR).glob("defer-log-*.jsonl")):
        try:
            lines = p.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("id"):
                n[str(row["id"])] += -1 if row.get("undo") else 1
    return {k: v for k, v in n.items() if v > 0}
