#!/usr/bin/env python3
"""closeshell.py — the /closeshell hand-off helper (2026-11-01).

JM closes a terminal; the work in it must not vanish. cc now runs sessions on
ix (or in a local tmux) so the *process* survives; this script carries the
*state* into dtd, where the next session (or JM) picks it up:

  status
      JSON: this session's id, host, whether it runs where closing the tab is
      safe (ix, or inside tmux), and the claims it holds with their state.
  handoff <task id> "<note>"
      Post a 😈 hand-off note (what is done, what is left, where the files are)
      on a task this session holds. The claim itself is marked by
      agent_claims.py done / release afterwards, by the skill.
  followup "<task (N) [N] @code>" --note "<context>"
      Create the follow-up in Todoist, due today (todo-fast.py: same parsing
      as /todo), claim it to this session and release it at once, so dtd
      shows it at the top with 🕐 (waiting on JM, RELEASED_TTL), and post the
      context as a 😈 note. A later `/d <task>` on ix turns 🕐 back into 😈.

Every command prints one JSON line. Nothing here completes a Todoist task.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = Path.home() / "i446-monorepo" / "lib"
NOTE_PREFIX = "😈 "


def _mod(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _todoist():
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    import todoist
    return todoist


def session_id() -> str:
    return os.environ.get("CLAUDE_CODE_SESSION_ID", "")


def where() -> dict:
    """Where this session's process lives and whether closing the tab is safe:
    on ix every cc runs in tmux; elsewhere only a tmux-wrapped cc survives."""
    host = socket.gethostname().split(".")[0].lower()
    in_tmux = bool(os.environ.get("TMUX") or os.environ.get("CC_IN_TMUX"))
    on_ix = host.startswith("ix")
    return {"host": host, "in_tmux": in_tmux, "on_ix": on_ix,
            "survives_close": on_ix or in_tmux}


def status(ac=None) -> dict:
    ac = ac or _mod("agent_claims")
    sid = session_id()
    claims = ac.load()
    mine = [dict(task_id=tid, task=r.get("task", ""), state=r.get("state"), done=r.get("done"))
            for tid, r in claims.items() if r.get("session") == sid]
    return {"ok": True, "session": sid, **where(), "claims": mine}


def handoff(task_id: str, note: str, post=None) -> dict:
    post = post or _todoist().add_comment
    post(task_id, NOTE_PREFIX + note.strip())
    return {"ok": True, "task_id": task_id, "noted": True}


def followup(text: str, note: str = "", ac=None, tf=None, post=None) -> dict:
    """Create → claim → release → note. The claim/release pair is what puts the
    row in dtd's claimed tier (🕐) instead of the plain list."""
    ac = ac or _mod("agent_claims")
    tf = tf or _mod("todo-fast")
    sid = session_id()
    if not sid:
        return {"ok": False, "error": "no CLAUDE_CODE_SESSION_ID; run inside Claude"}
    r = tf.create(text)
    task = r["task"]
    ac.claim(task, sid, also=True)      # also: keep the session's own claim(s)
    tid = str(task["id"])
    ac.set_state(tid, "released", None, ac.CLAIMS_DIR)
    ac._set_held(sid, [i for i in ac.held(sid) if i != tid], ac.CLAIMS_DIR)
    noted = False
    if note.strip():
        try:
            (post or _todoist().add_comment)(tid, NOTE_PREFIX + note.strip())
            noted = True
        except Exception:  # noqa: BLE001
            pass
    try:
        ac.push()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "task_id": tid, "content": r["content"], "labels": r["labels"],
            "state": "released", "noted": noted}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip()); return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "status":
        print(json.dumps(status(), ensure_ascii=False)); return 0
    if cmd == "handoff" and len(rest) >= 2:
        print(json.dumps(handoff(rest[0], " ".join(rest[1:])), ensure_ascii=False)); return 0
    if cmd == "followup" and rest:
        note = ""
        if "--note" in rest:
            i = rest.index("--note"); note = " ".join(rest[i + 1:]); rest = rest[:i]
        out = followup(" ".join(rest), note)
        print(json.dumps(out, ensure_ascii=False)); return 0 if out.get("ok") else 1
    print(json.dumps({"ok": False, "error": "usage: status | handoff <task id> <note> | followup <task> [--note <context>]"}))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
