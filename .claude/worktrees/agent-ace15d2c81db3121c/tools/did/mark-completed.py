#!/usr/bin/env python3
"""Append habit/task names to ~/.local/state/jm/completed-today.json with guards.

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
from pathlib import Path

import sys as _sys; _sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib")); import state_paths as _sp
import daytime  # noqa: E402  shared "now"/"today" resolution — see lib/daytime.py
COMPLETED = _sp.COMPLETED_TODAY

# Annotation + punctuation strip for duplicate-detection key (Step 6 posthoc guard).
# Keep in sync with the tokenize() rules in test_did_routing.py so both use the
# same normalization.
_ANNOT_RE = re.compile(r"\s*[\[\(\{][^\]\)\}]*[\]\)\}]")


def _normalize(name: str) -> str:
    return name.strip().lower()


def _dup_key(name: str) -> str:
    """Normalization used for same-day posthoc duplicate detection.

    Lowercase, strip [N]/(N)/{N} annotations, collapse internal whitespace.
    Posthoc content like `talk with richard [20]` normalizes to
    `talk with richard` and compares equal regardless of surrounding
    annotation churn.
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


def append_names(new_names: list[str], *, today: str | None = None,
                 path: Path | None = None, points: dict | None = None,
                 ids: dict | None = None) -> dict:
    """Append names to completed-today.json with dedup + date gate.

    Returns the resulting dict (unwritten if path is a stub, written if path is real).
    Uses flock on the file to serialize concurrent writers.

    `points` is an optional dict mapping name -> int (分 value). Merged into
    the "points" key in the JSON so enrichment scripts can sum per-block 分.

    `ids` is an optional dict mapping name -> Todoist task id. Merged into the
    "ids" key so dtd can hide a completed task by its id, not just its name — a
    completed task then never suppresses a *different* still-open task that
    shares the same annotation-stripped name (regression 2026-06-26: completing
    `stats [10]` hid an unrelated open `stats`).

    `path` resolves to the module-level `COMPLETED` constant at call time when
    omitted. Re-reading it via the module means tests can monkey-patch
    `mc.COMPLETED` and have both CLI and library paths honor the override.
    """
    today = today or daytime.today_iso()
    if path is None:
        path = COMPLETED
    path.parent.mkdir(parents=True, exist_ok=True)

    # Open with O_CREAT so we can lock even if the file doesn't exist yet.
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = _load(path)

        # Date gate: only a genuinely NEWER date resets names — a stored date
        # that is equal-or-newer than `today` is kept as-is. ISO YYYY-MM-DD
        # strings compare lexicographically the same as chronologically, so
        # plain string `>` is a correct forward-only check without needing a
        # real date parse. This used to be `!=`, which wiped the day's
        # completions on ANY mismatch, including a date moving BACKWARD
        # (an OS TZ correction, an International Date Line crossing, or the
        # two-machine clock disagreement absorb_remote() below also has to
        # tolerate) — during travel this fired routinely and silently
        # re-opened already-completed habits for re-completion, double-
        # crediting Neon points. A day only truly ends when `today` moves
        # strictly past what's stored.
        if today > data.get("date", ""):
            data = {"date": today, "names": [], "points": {}, "ids": {}}

        # Ensure points/timestamps/ids dicts exist (backwards compat)
        if "points" not in data:
            data["points"] = {}
        if "timestamps" not in data:
            data["timestamps"] = {}
        if "ids" not in data:
            data["ids"] = {}

        # Dedup existing names first (self-heal any pre-existing dupes).
        data["names"] = _dedup_preserve_order(data["names"])

        now_hhmm = daytime.local_now().strftime("%H:%M")
        existing_keys = {_normalize(n) for n in data["names"]}
        for raw in new_names:
            k = _normalize(raw)
            if not k or k in existing_keys:
                continue
            existing_keys.add(k)
            data["names"].append(k)  # store lowercased/normalized form
            data["timestamps"][k] = now_hhmm  # record completion time

        # Merge points
        if points:
            for name, pts in points.items():
                k = _normalize(name)
                if k and pts:
                    data["points"][k] = pts

        # Merge name -> Todoist id map (id-based dtd hide)
        if ids:
            for name, tid in ids.items():
                k = _normalize(name)
                if k and tid:
                    data["ids"][k] = str(tid)

        _atomic_write(path, data)
        # Cross-machine mirror (2026-07-30): completed-today is machine-local,
        # so a completion recorded on ix (janus-mobile swipe → did-fast) never
        # hid the card in Straylight's dtd until the ~3min cache daemon
        # re-pulled Todoist. Mirror this host's record to a per-host file in
        # the synced vault (single WRITER per file — never write another
        # host's; Syncthing last-writer-wins clobbers are this week's lesson).
        # dtd's watcher absorbs remote hosts' files via absorb_remote().
        # Real COMPLETED path only, so tests with tmp paths stay isolated.
        if path == COMPLETED:
            _mirror_to_vault(data)
        return data
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def remove_names(names: list[str], *, path: Path | None = None) -> dict:
    """Remove names from completed-today.json (ctrl-z undo path).

    Removal is keyed on `_dup_key` (lowercase + annotation-strip) so an undo
    of `buy plants [20]` clears the stored `buy plants` entry. Also clears the
    matching `points`, `timestamps`, and `ids` keys. Locked with flock like
    append_names. Returns the resulting dict.
    """
    if path is None:
        path = COMPLETED
    if not path.exists():
        return {"date": "", "names": []}

    remove_keys = {_dup_key(n) for n in names if _dup_key(n)}

    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = _load(path)
        kept = [n for n in data["names"] if _dup_key(n) not in remove_keys]
        data["names"] = kept
        for bucket in ("points", "timestamps", "ids"):
            if isinstance(data.get(bucket), dict):
                data[bucket] = {k: v for k, v in data[bucket].items()
                                if _dup_key(k) not in remove_keys}
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
    entries stored under a date OLDER than today are treated as absent (a new
    day clears the dup-set) — but a stored date that is equal-or-newer than
    `today` still applies (matches append_names' forward-only gate below;
    see its comment for why this can't be a plain equality check).
    """
    today = today or daytime.today_iso()
    if path is None:
        path = COMPLETED
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or today > data.get("date", ""):
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


MIRROR_DIR = Path.home() / "vault" / "z_ibx"


def _host_slug() -> str:
    import socket
    return re.sub(r"[^a-z0-9]+", "-", socket.gethostname().lower()).strip("-")[:24]


def _mirror_path() -> Path:
    return MIRROR_DIR / f"completed-today-{_host_slug()}.json"


def _mirror_to_vault(data: dict) -> None:
    """Best-effort write of this host's completed-today record to the synced
    vault. Never raises — a sync mirror must not fail the completion."""
    try:
        MIRROR_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write(_mirror_path(), data)
    except Exception:
        pass


def absorb_remote(today: str | None = None) -> int:
    """Merge OTHER hosts' synced completed-today-*.json into the local file.

    Returns how many names were newly absorbed. Date-gated: a remote file
    dated strictly OLDER than this machine's `today` is ignored — but a
    remote date that is equal-or-newer still merges (see append_names'
    forward-only gate comment). Two travel machines routinely disagree on
    the calendar date for hours at a time (Ix stays home, Straylight follows
    local time); a plain equality check silently dropped every legitimate
    cross-machine completion for that whole window. Called by dtd's watcher
    when a remote mirror's mtime advances (a completion on another machine
    just synced in).
    """
    today = today or daytime.today_iso()
    own = _mirror_path().name
    absorbed = 0
    for p in sorted(MIRROR_DIR.glob("completed-today-*.json")):
        if p.name == own:
            continue
        try:
            with open(p, encoding="utf-8") as f:
                remote = json.load(f)
        except Exception:
            continue
        if not isinstance(remote, dict) or today > remote.get("date", ""):
            continue
        names = [n for n in remote.get("names", []) if n]
        if not names:
            continue
        before = len(_load(COMPLETED).get("names", []))
        merged = append_names(names, today=today,
                              points=remote.get("points") or None,
                              ids=remote.get("ids") or None)
        absorbed += max(0, len(merged.get("names", [])) - before)
    return absorbed


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--absorb-remote":
        n = absorb_remote()
        print(f"absorbed={n}")
        return 0
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
    if len(argv) >= 2 and argv[1] == "--remove":
        if len(argv) < 3:
            print("usage: mark-completed.py --remove <name> [<name2> ...]", file=sys.stderr)
            return 2
        result = remove_names(argv[2:])
        print(f"date={result['date']} count={len(result['names'])}")
        return 0
    if len(argv) < 2:
        print("usage: mark-completed.py <name> [<name2> ...]", file=sys.stderr)
        return 2
    result = append_names(argv[1:])
    print(f"date={result['date']} count={len(result['names'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
