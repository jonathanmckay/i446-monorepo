#!/usr/bin/env python3
"""reap-idle-claude.py: end idle Claude Code sessions on Ix before they pile up.

    reap-idle-claude.py [--dry-run] [--hours N] [--waiting-hours N] [--claim-hours N]
                        [--max-live N] [--cap-min-idle N]

2026-11-08 ("fix Ix memory"): jetsam killed claude.exe at 03:03 on 11-06 with
swap at 98%. No single spawner: ~30 interactive sessions idle for days (each
claude plus its MCP node servers, 25+ GB together) left no room for the overnight
jobs. Cron runs this at 02:45 (config/ix.crontab).

2026-11-14 ("cap MCP node servers"): jetsam again on 11-13 (node x135 8.6 GB,
claude.exe x28 7.9 GB). 28 sessions were live because the 02:45 run with a 48 h
bar reaped 4-5 a day while batches of four arrived every morning, and 8 of 27
were exempt for holding a dtd claim at 79-199 h idle. A claim is a file keyed by
session id (tools/did/agent_claims.py); it survives the process, and `cc <title>`
resumes the transcript with it intact, so the exemption became a bar. Cron now
runs hourly (config/ix.crontab).

A session is reaped when all hold:
  - its ~/.claude/sessions/<pid>.json is kind "interactive" (bg jobs never),
  - status is "idle" or "waiting", and statusUpdatedAt is older than its bar:
      --hours (6)          idle, no dtd claim
      --waiting-hours (24) a pending permission prompt: killing it aborts the
                           in-flight tool call, which a resume does not redo
      --claim-hours (24)   holds an open dtd claim (agent_claims.py); when the
                           claim list cannot be read, claimed-or-not is unknown
                           and nothing is reaped
    the highest applicable bar wins,
  - the live pid's start time matches the file's procStart (UTC), so a reused
    pid is never killed,
  - it is not this process or one of its ancestors.
Cap pass: if more than --max-live (12) interactive sessions would still be
alive, the oldest "idle" sessions idle for at least --cap-min-idle (1) h go too,
claimed or not, until the count fits. The age floor keeps a session that just
finished its first turn, or a batch launched this minute, out of the cap.
Its whole process tree is captured before SIGTERM (children re-parent to
launchd once the session dies), then survivors get SIGKILL after a grace.
One JSON line per decision goes to ~/Library/Logs/reap-idle-claude.jsonl.

Second pass (2026-11-08, "Termius thread picker"): a cc-* tmux session whose
Claude has exited sits at a bare zsh forever (claude-tracked drops to a shell),
and 30 of them padded the phone's tmux session list to ~60 rows. Kill every
cc-* tmux session that no client is attached to and whose panes are all a
shell with no child processes.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
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
SHELLS = ("zsh", "-zsh", "bash", "-bash", "sh")
GRACE = 10
# cron's PATH is /usr/bin:/bin, so a bare "tmux" found nothing there and the dead-tmux pass
# silently did nothing from 11-08 to 11-14 (every log line said 0 killed with 18 to kill).
TMUX = shutil.which("tmux") or "/opt/homebrew/bin/tmux"


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


def candidates(hours: float, now: float, table: dict[int, int], held: set[str], me: set[int],
               waiting_hours: float | None = None, claim_hours: float | None = None) -> list[dict]:
    """One row per live interactive-or-not session file; skip=None means reap."""
    waiting_hours = hours if waiting_hours is None else waiting_hours
    claim_hours = hours if claim_hours is None else claim_hours
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
        claimed = s.get("sessionId") in held
        bar, tags = hours, []
        if s.get("status") == "waiting":
            bar, tags = max(bar, waiting_hours), tags + ["waiting"]
        if claimed:
            bar, tags = max(bar, claim_hours), tags + ["claimed"]
        why = None
        if s.get("kind") != "interactive":
            why = f"kind {s.get('kind')}"
        elif s.get("status") not in REAPABLE:
            why = f"status {s.get('status')}"
        elif "*" in held:
            why = "holds a dtd claim"      # claim list unreadable: assume every session does
        elif idle_h < bar:
            why = f"idle {idle_h:.0f}h" + (", " + ", ".join(tags) if tags else "")
        elif pid in me:
            why = "this session"
        elif " ".join((s.get("procStart") or "").split()) != lstart_utc(pid):
            why = "pid reused"
        out.append({"pid": pid, "session": s.get("sessionId"), "name": s.get("name"),
                    "status": s.get("status"), "idle_h": round(idle_h), "claimed": claimed, "skip": why})
    return out


def cap(rows: list[dict], max_live: int, min_idle_h: float) -> list[dict]:
    """Flip the oldest still-skipped "idle" rows to reap until no more than max_live
    interactive sessions survive. Only rows skipped for being under an idle bar and idle
    at least min_idle_h qualify; busy, shell, waiting, bg, this-session and pid-reused
    rows never do. Returns the rows it flipped (each gets "cap": True)."""
    survivors = [r for r in rows if r["skip"] and not r["skip"].startswith("kind ")]
    excess = len(survivors) - max_live
    pool = sorted((r for r in survivors
                   if r["status"] == "idle" and r["skip"].startswith("idle ") and r["idle_h"] >= min_idle_h),
                  key=lambda r: -r["idle_h"])
    flipped = pool[:max(excess, 0)]
    for r in flipped:
        r["skip"], r["cap"] = None, True
    return flipped


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


def tmux_panes() -> list[tuple[str, int, str, int]]:
    """(session, clients attached, pane foreground command, pane pid) for every pane; [] without tmux."""
    try:
        out = subprocess.run([TMUX, "list-panes", "-a", "-F",
                              "#{session_name}\t#{session_attached}\t#{pane_current_command}\t#{pane_pid}"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 4 and parts[1].isdigit() and parts[3].isdigit():
            rows.append((parts[0], int(parts[1]), parts[2], int(parts[3])))
    return rows


def dead_tmux(panes: list[tuple[str, int, str, int]], table: dict[int, int]) -> list[str]:
    """Detached cc-* sessions whose every pane is an idle shell (no children)."""
    by: dict[str, list[bool]] = {}
    for name, attached, cmd, pid in panes:
        if not name.startswith("cc-"):
            continue
        by.setdefault(name, []).append(not attached and cmd in SHELLS and not descendants(pid, table))
    return sorted(n for n, ok in by.items() if all(ok))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hours", type=float, default=6, help="idle bar for unclaimed idle sessions")
    ap.add_argument("--waiting-hours", type=float, default=24, help="bar for a pending permission prompt")
    ap.add_argument("--claim-hours", type=float, default=24, help="bar for sessions holding a dtd claim")
    ap.add_argument("--max-live", type=int, default=12, help="cap on surviving interactive sessions")
    ap.add_argument("--cap-min-idle", type=float, default=1, help="hours idle before the cap may take a session")
    a = ap.parse_args(argv)
    now = time.time()
    table = ps_table()
    rows = candidates(a.hours, now, table, held_sessions(), ancestors(os.getpid(), table),
                      waiting_hours=a.waiting_hours, claim_hours=a.claim_hours)
    capped = cap(rows, a.max_live, a.cap_min_idle)
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
        dead = dead_tmux(tmux_panes(), ps_table())
        for name in dead:
            if not a.dry_run:
                subprocess.run([TMUX, "kill-session", "-t", f"={name}"], capture_output=True)
            f.write(json.dumps({"ts": ts, "dry_run": a.dry_run, "action": "kill-tmux", "tmux": name}) + "\n")
    for r in rows:
        if not r["skip"]:
            print(f"{'would reap' if a.dry_run else 'reaped'} {r['pid']} {r['name'] or ''} idle {r['idle_h']}h"
                  + (" (claimed)" if r.get("claimed") else "") + (" (cap)" if r.get("cap") else ""))
    if doomed or dead or a.dry_run:   # hourly cron: stay quiet when there is nothing to say
        print(f"{len(doomed)} of {len(rows)} live sessions {'to reap' if a.dry_run else 'reaped'}"
              + (f" ({len(capped)} by the {a.max_live}-session cap)" if capped else ""))
        print(f"{len(dead)} dead cc-* tmux sessions {'to kill' if a.dry_run else 'killed'}"
              + (f": {' '.join(dead)}" if dead else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
