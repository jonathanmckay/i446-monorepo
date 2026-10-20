#!/usr/bin/env python3
"""bump-fast.py <task-id>... — set Todoist priority to p1 (API value 4).

Backs dtd's ⌥↑ (2026-10-04). dtd patches its own list snapshot first so the
task jumps to the top at once; this runs detached and makes it real. Exit 1
if any update failed (dtd then flags it in the header).
"""
import fcntl
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).parent


def _load(mod_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(mod_name, _HERE / filename)
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = m
    spec.loader.exec_module(m)
    return m


P1 = 4  # Todoist API: 4 = p1 (highest) ... 1 = p4


LIVE_CACHE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "jm" / "task-queue.json"


def bump(ids, api) -> list:
    """POST priority p1 for each id; returns the ids that succeeded."""
    ok = []
    for tid in ids:
        try:
            api("POST", f"/tasks/{tid}", {"priority": P1})
            ok.append(tid)
        except Exception as e:  # noqa: BLE001
            print(f"bump {tid}: {e}", file=sys.stderr)
    return ok


def patch_live_cache(ids, path: Path = LIVE_CACHE, wait_s: float = 5.0) -> int:
    """Set priority 4 on these ids in the LIVE task cache, under the same
    flock refresh_task_queue holds. dtd's watcher copies this file over its
    list snapshot whenever it changes; without the patch a background refresh
    landing before Todoist reflected the change (the periodic refresher keeps
    the 'today' bucket's entries as they were) would drop the task back down.
    Returns how many entries were patched; best-effort, never raises."""
    want = {str(i) for i in ids}
    try:
        with open(path.with_suffix(".lock"), "w") as lk:
            deadline = time.monotonic() + wait_s
            while True:
                try:
                    fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        return 0
                    time.sleep(0.1)
            try:
                data = json.loads(path.read_text())
                n = 0
                for v in data.values():
                    if isinstance(v, list):
                        for t in v:
                            if isinstance(t, dict) and str(t.get("id")) in want:
                                t["priority"] = P1
                                n += 1
                if n:
                    tmp = path.with_suffix(".json.bump")
                    tmp.write_text(json.dumps(data, ensure_ascii=False))
                    tmp.replace(path)
                return n
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)
    except Exception as e:  # noqa: BLE001
        print(f"live cache patch failed: {e}", file=sys.stderr)
        return 0


def main(argv) -> int:
    ids = [a for a in argv if a and not a.startswith("BLOCK:")]
    if not ids:
        return 0
    df = _load("points_fast", "points-fast.py")._df
    ok = bump(ids, df._api)
    if ok:
        patch_live_cache(ok)
    return 0 if len(ok) == len(ids) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
