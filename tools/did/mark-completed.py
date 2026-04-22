#!/usr/bin/env python3
"""Append habit/task names to ~/vault/z_ibx/completed-today.json with guards.

Usage:
    python3 mark-completed.py <name> [<name2> ...]
    python3 mark-completed.py --check <name>      # exit 0 if dup found, 1 otherwise

Guards:
    - Atomic write via .tmp + os.replace
    - Case-insensitive + whitespace-stripped dedup (order preserved)
    - Date gate: if stored date < today, names reset to [] before append
    - Locked with fcntl to block concurrent writers on macOS/Linux

Duplicate-check (--check mode):
    Used by /did Step 6 (variable task) to detect same-day duplicate posthocs.
    Normalizes input (lowercase, strip [N]/(N)/{N}, strip punctuation) and
    compares against the today-bucket of completed-today.json. Prints "dup"
    with the matched name on stdout and exits 0 if a duplicate is detected;
    prints "no-dup" and exits 1 if the normalized key is fresh.

Exit: prints resulting unique count to stdout, returns 0 on success.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

COMPLETED = Path.home() / "vault/z_ibx/completed-today.json"

# Annotation + punctuation strip for duplicate-detection key (Step 6 posthoc guard).
# Keep in sync with next-task.py::strip_task_name and the tokenize() rules in
# test_did_routing.py so all three use the same normalization.
_ANNOT_RE = re.compile(r"\s*[\[\(\{][^\]\)\}]*[\]\)\}]")


def _normalize(name: str) -> str:
    return name.strip().lower()


def _dup_key(name: str) -> str:
    """Normalization used for same-day posthoc duplicate detection.

    Lowercase, strip [N]/(N)/{N} annotations, collapse internal whitespace.
    Matches the stripping done by next-task.py::strip_task_name so posthoc
    content like `talk with richard [20]` normalizes to `talk with richard`
    and compares equal regardless of surrounding annotation churn.
    """
    s = name.lower().strip()
    s = _ANNOT_RE.sub("", s)
    # Collapse multiple spaces but keep words/punctuation intact — we want
    # "talk with richard" and "talk  with richard" to match.
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _dedup_preserve_order(names: list[str]) -> list[str]:
    """Remove duplicates (case-insensitive, whitespace-trimmed), preserve first occurrence."""
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        k = _normalize(n)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(n)
    return out


def _load(path: Path) -> dict:
    if not path.exists():
        return {"date": "", "names": []}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"date": "", "names": []}
    if not isinstance(data, dict):
        return {"date": "", "names": []}
    data.setdefault("date", "")
    names = data.get("names", [])
    if not isinstance(names, list):
        names = []
    data["names"] = [str(n) for n in names]
    return data


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def append_names(new_names: list[str], *, today: str | None = None, path: Path | None = None) -> dict:
    """Append names to completed-today.json with dedup + date gate.

    Returns the resulting dict (unwritten if path is a stub, written if path is real).
    Uses flock on the file to serialize concurrent writers.

    `path` resolves to the module-level `COMPLETED` constant at call time when
    omitted. Re-reading it via the module means tests can monkey-patch
    `mc.COMPLETED` and have both CLI and library paths honor the override.
    """
    today = today or date.today().isoformat()
    if path is None:
        path = COMPLETED
    path.parent.mkdir(parents=True, exist_ok=True)

    # Open with O_CREAT so we can lock even if the file doesn't exist yet.
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = _load(path)

        # Date gate: different date → reset names.
        if data.get("date") != today:
            data = {"date": today, "names": []}

        # Dedup existing names first (self-heal any pre-existing dupes).
        data["names"] = _dedup_preserve_order(data["names"])

        existing_keys = {_normalize(n) for n in data["names"]}
        for raw in new_names:
            k = _normalize(raw)
            if not k or k in existing_keys:
                continue
            existing_keys.add(k)
            data["names"].append(k)  # store lowercased/normalized form

        _atomic_write(path, data)
        return data
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def is_duplicate_today(name: str, *, today: str | None = None, path: Path | None = None) -> str | None:
    """Check whether `name` is already recorded in today's completed-today bucket.

    Used by /did Step 6 (variable task, no Todoist match) BEFORE creating a new
    posthoc, to prevent duplicate posthocs when the same /did is invoked twice
    in one day (accidentally, from multiple TUIs, or after user forgot). The
    comparison uses `_dup_key` (annotation + punctuation strip) so that
    `talk with richard [20]` matches `talk with richard` already stored.

    Returns the matched stored name on duplicate, None otherwise. Date-gated:
    entries stored under a different date are treated as absent (a new day
    clears the dup-set).
    """
    today = today or date.today().isoformat()
    if path is None:
        path = COMPLETED
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("date") != today:
        return None
    stored = data.get("names", [])
    if not isinstance(stored, list):
        return None
    key = _dup_key(name)
    if not key:
        return None
    for existing in stored:
        if _dup_key(str(existing)) == key:
            return str(existing)
    return None


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--check":
        if len(argv) < 3:
            print("usage: mark-completed.py --check <name>", file=sys.stderr)
            return 2
        name = " ".join(argv[2:])
        hit = is_duplicate_today(name)
        if hit is not None:
            print(f"dup\t{hit}")
            return 0
        print("no-dup")
        return 1
    if len(argv) < 2:
        print("usage: mark-completed.py <name> [<name2> ...]", file=sys.stderr)
        return 2
    result = append_names(argv[1:])
    print(f"date={result['date']} count={len(result['names'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
