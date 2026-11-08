#!/usr/bin/env python3
"""done_session — /done: close the task this Claude session claimed.

    done_session.py

The task: every claim of $CLAUDE_CODE_SESSION_ID that is held or marked ✅
(`agent_claims.py done` drops the id from the session index, so held() alone is
usually empty by the time JM types /done; a 'released' claim is a task this
session let go of, never closed here). Each is completed exactly as dtd's ⌥↵
does it, `did-fast.py --task-id <id> <content>`, which credits its points,
closes Todoist, clears the claim and stops the running Toggl timer only when
the timer is that task. Content comes from the dtd cache, not the claim
record: a card renamed since the claim would otherwise miss its timer.

Prints one JSON object: {"status": "done"|"no-claim"|"failed", "tasks": [...]}.
"done" means every task really closed (Todoist included), so the conversation
is safe to /clear.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DID_FAST = HERE / "did-fast.py"
TASK_QUEUE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "jm" / "task-queue.json"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_for_done", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def session_tasks(session: str, claims: dict[str, dict], held: list[str]) -> list[str]:
    """Held ids first, then this session's ✅ claims, no duplicates."""
    done = [tid for tid, r in claims.items()
            if r.get("session") == session and r.get("state") == "done"]
    return list(dict.fromkeys(held + done))


def current_content(tid: str, claims: dict[str, dict], tasks: list[dict]) -> str:
    live = next((t for t in tasks if str(t.get("id")) == tid), None)
    return (live or {}).get("content") or claims.get(tid, {}).get("task", "")


def closed_ok(rc: int, raw: str, tid: str, content: str, outcome) -> tuple[bool, str]:
    """(really closed, display). dtd_outcome's 'ok' only drops the ✓ when the
    Todoist close failed, so the close is checked here too."""
    if rc != 0 or not raw.strip():
        return False, f"{content} (did-fast exit {rc}, no output)"
    out = outcome.parse_output(raw)
    (_, status, display), = outcome.classify(out, [(tid, content)])
    if status in ("already", "recorded"):
        return True, display
    if status != "ok":
        return False, display
    hit = next((r for r in (out or {}).get("results") or []
                if str((r.get("todoist") or {}).get("id")) == tid), None)
    if hit is not None and not (hit.get("todoist") or {}).get("closed"):
        return False, f"{display} (Todoist close failed)"
    return True, display


def timer_line(raw: str) -> str | None:
    """What did-fast did with the running timer: the stopped entry, or None."""
    try:
        t = json.loads(raw).get("toggl_stopped")
    except (ValueError, AttributeError):
        return None
    if isinstance(t, dict) and t.get("stopped"):
        return f"stopped {t.get('description', '')} ({t.get('minutes')}m)"
    return None


def main() -> int:
    session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not session:
        print(json.dumps({"status": "failed", "error": "no CLAUDE_CODE_SESSION_ID; run inside Claude"}))
        return 1
    ac, outcome = _load("agent_claims"), _load("dtd_outcome")
    claims = ac.load()
    ids = session_tasks(session, claims, ac.held(session))
    if not ids:
        print(json.dumps({"status": "no-claim", "tasks": []}))
        return 0
    try:
        tasks = ac.all_tasks(json.loads(TASK_QUEUE.read_text()))
    except (OSError, ValueError):
        tasks = []
    report, all_ok = [], True
    for tid in ids:
        content = current_content(tid, claims, tasks)
        p = subprocess.run(["python3", str(DID_FAST), "--task-id", tid, content],
                           capture_output=True, text=True)
        ok, display = closed_ok(p.returncode, p.stdout, tid, content, outcome)
        all_ok &= ok
        report.append({"id": tid, "task": content, "ok": ok, "result": display,
                       "timer": timer_line(p.stdout)})
    print(json.dumps({"status": "done" if all_ok else "failed", "tasks": report},
                     ensure_ascii=False, indent=2))
    return 0 if all_ok else 2


if __name__ == "__main__":
    sys.exit(main())
