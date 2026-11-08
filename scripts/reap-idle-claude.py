#!/usr/bin/env python3
"""reap-idle-claude.py: end Claude Code sessions idle for 48 h+ before the overnight jobs.

    reap-idle-claude.py [--dry-run] [--hours N]

2026-11-08 ("fix Ix memory"): jetsam killed claude.exe at 03:03 on 11-06 with
swap at 98%. No single spawner: ~30 interactive sessions idle for days (each
claude plus its MCP node servers, 25+ GB together) left no room for the overnight
jobs. Cron runs this at 02:45 (config/ix.crontab).

A session is reaped when all hold:
  - its ~/.claude/sessions/<pid>.json is kind "interactive" (bg jobs never),
  - status is "idle" or "waiting" and statusUpdatedAt is >= --hours old,
  - the live pid's start time matches the file's procStart (UTC), so a reused
    pid is never killed,
  - it holds no open dtd claim (agent_claims.py), and it is not this process
    or one of its ancestors.
Its whole process tree is captured before SIGTERM (children re-parent to
launchd once the session dies), then survivors get SIGKILL after a grace.
One JSON line per decision goes to ~/Library/Logs/reap-idle-claude.jsonl.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
SESSIONS = HOME / ".claude" / "sessions"
CLAIMS = HOME / "i446-monorepo" / "tools" / "did" / "agent_claims.py"
LOG = HOME / "Library" / "Logs" / "reap-idle-claude.jsonl"
REAPABLE = ("idle", "waiting")
GRACE = 10


def ps_table() -> dict[int, int]:
    """pid -> ppid for every process."""
    out = subprocess.run(["/bin/ps", "-axo", "pid=,ppid="], capture_output=True, text=True).stdout
    table = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            table[int(parts[0])] = int(parts[1])
    return table


def lstart_utc(pid: int) -> str | None:
    r = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                       env={**os.environ, "TZ": "UTC"})
    return " ".join(r.stdout.split()) or None


def descendants(pid: int, table: dict[int, int]) -> list[int]:
    kids: dict[int, list[int]] = {}
    for p, pp in table.items():
        kids.setdefault(pp, []).append(p)
    out, todo = [], [pid]
    while todo:
        for k in kids.get(todo.pop(), []):
            out.append(k)
            todo.append(k)
    return out


def ancestors(pid: int, table: dict[int, int]) -> set[int]:
    seen = set()
    while pid and pid not in seen:
        seen.add(pid)
        pid = table.get(pid, 0)
    return seen


def held_sessions() -> set[str]:
    try:
        r = subprocess.run(["/usr/bin/python3", str(CLAIMS), "list"], capture_output=True, text=True, timeout=30)
        claims = json.loads(r.stdout)
    except Exception:  # noqa: BLE001
        return {"*"}   # can't tell who holds what: reap nothing
    return {c.get("session") for c in claims.values()
            if isinstance(c, dict) and c.get("state") != "released" and not c.get("done")}


def candidates(hours: float, now: float, table: dict[int, int], held: set[str], me: set[int]) -> list[dict]:
    out = []
    for f in sorted(SESSIONS.glob("*.json")):
        try:
            s = json.loads(f.read_text())
            pid = int(s["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if pid not in table:
            continue
        idle_h = (now - (s.get("statusUpdatedAt") or 0) / 1000) / 3600
        why = None
        if s.get("kind") != "interactive":
            why = f"kind {s.get('kind')}"
        elif s.get("status") not in REAPABLE:
            why = f"status {s.get('status')}"
        elif idle_h < hours:
            why = f"idle {idle_h:.0f}h"
        elif pid in me:
            why = "this session"
        elif "*" in held or s.get("sessionId") in held:
            why = "holds a dtd claim"
        elif " ".join((s.get("procStart") or "").split()) != lstart_utc(pid):
            why = "pid reused"
        out.append({"pid": pid, "session": s.get("sessionId"), "name": s.get("name"),
                    "status": s.get("status"), "idle_h": round(idle_h), "skip": why})
    return out


def reap(pids: list[int], table: dict[int, int]) -> dict[int, int]:
    """SIGTERM each session and its tree, SIGKILL whatever is left after GRACE.
    Returns pid -> number of processes still alive (0 = clean)."""
    trees = {p: [p] + descendants(p, table) for p in pids}
    for tree in trees.values():
        for q in tree:
            try:
                os.kill(q, signal.SIGTERM)
            except ProcessLookupError:
                pass
    time.sleep(GRACE)
    left = {}
    for p, tree in trees.items():
        n = 0
        for q in tree:
            try:
                os.kill(q, signal.SIGKILL)
                n += 1
            except ProcessLookupError:
                pass
        left[p] = n
    return left


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hours", type=float, default=48)
    a = ap.parse_args(argv)
    now = time.time()
    table = ps_table()
    rows = candidates(a.hours, now, table, held_sessions(), ancestors(os.getpid(), table))
    doomed = [r["pid"] for r in rows if not r["skip"]]
    killed = {} if a.dry_run or not doomed else reap(doomed, table)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        for r in rows:
            r.update(ts=ts, dry_run=a.dry_run, action="skip" if r["skip"] else "reap")
            if r["pid"] in killed:
                r["sigkilled"] = killed[r["pid"]]
            f.write(json.dumps(r) + "\n")
    for r in rows:
        if not r["skip"]:
            print(f"{'would reap' if a.dry_run else 'reaped'} {r['pid']} {r['name'] or ''} idle {r['idle_h']}h")
    print(f"{len(doomed)} of {len(rows)} live sessions {'to reap' if a.dry_run else 'reaped'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
