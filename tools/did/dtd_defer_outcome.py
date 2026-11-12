#!/usr/bin/env python3
"""dtd_defer_outcome.py: turn one defer-fast run into dtd's header line + a record.

    defer-fast stdout | dtd_defer_outcome.py <rc> <task_id> [reclog host sid]

stdout, exit 0: "→ <target_date> [<claimed>] today / [<remaining>] later", the
                tail of the ⏭ header, when defer-fast returned a result.
stdout, exit 1: a one-line reason otherwise: defer-fast's own {"error": ...}
                (it prints its failure as JSON on stdout: "task id X fetch
                failed: <urlopen error ...>", "task not found", an HTTP 4xx
                from Todoist), else the raw output, else the exit code.

Either way appends {"kind": "defer", "status": "ok"|"fail", ...} to the synced
per-host record log <reclog> (~/vault/z_ibx/dtd-log/<host>.jsonl) when one is
given, so a failed defer leaves evidence next to its "pick" record.

Why (2026-11-12): six ctrl-d defers on Straylight between 06:51 and 07:00 all
ended in "? defer failed: ... (restored to list)" and nothing else. The worker
sent defer-fast's stderr to /dev/null and only checked whether stdout parsed
as a success, so the error JSON defer-fast had printed was thrown away. The
cause (that machine's internet was down, per its own tailscale watchdog log)
took cross-host log archaeology to establish. Now the header says why and the
record log keeps it.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

MAX_REASON = 160


def outcome(raw: str, rc: int) -> tuple[bool, str, str | None]:
    """(ok, line, target_date): the header tail on success, a one-line reason
    on failure."""
    data = None
    try:
        data = json.loads(raw)
    except Exception:
        pass
    if isinstance(data, dict) and data.get("target_date"):
        line = (f"→ {data['target_date']} [{data.get('claimed_points', '?')}] today"
                f" / [{data.get('remaining_points', '?')}] later")
        return True, line, str(data["target_date"])
    if isinstance(data, dict) and data.get("error"):
        why = str(data["error"])
    elif raw.strip():
        why = raw.strip()
    else:
        why = f"defer-fast exited {rc} with no output (stderr in the dtd .err log)"
    why = re.sub(r"\s+", " ", why).strip()
    if len(why) > MAX_REASON:
        why = why[:MAX_REASON - 1] + "…"
    return False, why, None


def record(path: str, host: str, sid: str, task_id: str, ok: bool, line: str,
           rc: int, target: str | None) -> None:
    """Append the kind=defer record; a missing path (tests) or an unwritable
    log never fails the defer."""
    if not path:
        return
    rec = {"ts": time.time(), "host": host, "sid": sid, "kind": "defer",
           "status": "ok" if ok else "fail", "id": task_id, "rc": rc}
    if ok:
        rec["target_date"] = target
    else:
        rec["error"] = line
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: dtd_defer_outcome.py <rc> <task_id> [reclog host sid]",
              file=sys.stderr)
        return 2
    try:
        rc = int(argv[0])
    except ValueError:
        rc = -1
    task_id = argv[1]
    reclog, host, sid = (argv[2:5] + ["", "", ""])[:3]
    ok, line, target = outcome(sys.stdin.read(), rc)
    record(reclog, host, sid, task_id, ok, line, rc, target)
    print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
