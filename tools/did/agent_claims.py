#!/usr/bin/env python3
"""agent_claims — tie a dtd task to the Claude session working on it.

/claim <task>[: prompt] links the CURRENT Claude session to a task (2026-10-05).
While that session is mid-turn, dtd greys the task out with 😈 and its top
line shows a spinner; when the turn ends the task looks normal again.

Store: ~/vault/z_ibx/agent-claims/ (Syncthing carries it to Ix, where dtd runs)
  <task_id>.json          {task_id, task, session, host, cwd, claimed_at}
  <task_id>.state         "working <epoch>" | "idle <epoch>"
  by-session/<session>    the task_id this session holds (one task per session)

Writers: this CLI (claim/release) and agent-claim-hook.sh (UserPromptSubmit →
working, Stop → idle, SessionEnd → release). Every write is tmp + rename so the
directory mtime advances; dtd's watcher reloads on that.

Usage:
  agent_claims.py claim <query>   fuzzy-match a task in the dtd cache, claim it
  agent_claims.py hook            UserPromptSubmit fast path for `/d <task>: ...`
  agent_claims.py new <task>      create a today task and claim it
  agent_claims.py release         drop this session's claim
  agent_claims.py list            JSON of claims (+ derived working flag)
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import time
from pathlib import Path

CLAIMS_DIR = Path.home() / "vault" / "z_ibx" / "agent-claims"
# A turn with no Stop for this long is a crashed/killed session, not work.
STALE_SECS = 3600


# --- store ---------------------------------------------------------------
def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def set_state(task_id: str, state: str, now: float | None = None,
              root: Path = CLAIMS_DIR) -> None:
    _write(root / f"{task_id}.state", f"{state} {int(now or time.time())}\n")


def release(session: str, root: Path = CLAIMS_DIR) -> str | None:
    idx = root / "by-session" / session
    try:
        tid = idx.read_text().strip()
    except OSError:
        return None
    for p in (root / f"{tid}.json", root / f"{tid}.state", idx):
        try:
            p.unlink()
        except OSError:
            pass
    return tid


def claim(task: dict, session: str, root: Path = CLAIMS_DIR,
          now: float | None = None) -> dict:
    """One task per session: an earlier claim by this session is released."""
    release(session, root)
    tid = str(task["id"])
    rec = {"task_id": tid, "task": task.get("content", ""), "session": session,
           "host": socket.gethostname().split(".")[0], "cwd": os.getcwd(),
           "claimed_at": int(now or time.time())}
    _write(root / f"{tid}.json", json.dumps(rec, ensure_ascii=False) + "\n")
    set_state(tid, "working", now, root)   # /claim itself runs mid-turn
    _write(root / "by-session" / session, tid + "\n")
    return rec


def load(root: Path = CLAIMS_DIR, now: float | None = None) -> dict[str, dict]:
    """task_id -> claim record with 'state' and derived 'working'."""
    now = now or time.time()
    out: dict[str, dict] = {}
    try:
        files = list(root.glob("*.json"))
    except OSError:
        return out
    for f in files:
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        tid = str(rec.get("task_id") or f.stem)
        state, ts = "idle", 0
        try:
            s, t = (root / f"{tid}.state").read_text().split()[:2]
            state, ts = s, int(t)
        except (OSError, ValueError):
            pass
        rec["state"] = state
        rec["working"] = state == "working" and now - ts < STALE_SECS
        out[tid] = rec
    return out


def working_ids(root: Path = CLAIMS_DIR, now: float | None = None) -> set[str]:
    return {tid for tid, r in load(root, now).items() if r["working"]}


# --- task matching ---------------------------------------------------------
def _did():
    spec = importlib.util.spec_from_file_location(
        "did_fast_for_claims", Path(__file__).resolve().parent / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_for_claims"] = mod
    spec.loader.exec_module(mod)
    return mod


def all_tasks(tq: dict) -> list[dict]:
    seen, out = set(), []
    for sec in tq.values():
        if not isinstance(sec, list):
            continue
        for t in sec:
            if isinstance(t, dict) and t.get("id") and t["id"] not in seen:
                seen.add(t["id"])
                out.append(t)
    return out


def candidates(query: str, tasks: list[dict], df) -> list[dict]:
    """Ranked matches: exact id, then substring of the cleaned name, then
    did-fast's word-overlap matcher."""
    q = query.strip().lower()
    for t in tasks:
        if str(t["id"]).lower() == q:
            return [t]
    clean = lambda t: df.ANNOT_RE.sub("", t.get("content", "")).strip().lower()
    exact = [t for t in tasks if clean(t) == q]
    if exact:
        return exact
    subs = [t for t in tasks if q in clean(t)]
    if subs:
        return sorted(subs, key=lambda t: len(clean(t)))
    best = df.match_todoist_task(query, tasks)
    return [best] if best else []


def push() -> None:
    """Mirror the store to Ix now (scripts/agent-claims-push.sh), detached."""
    import subprocess
    try:
        subprocess.Popen([str(Path.home() / "i446-monorepo/scripts/agent-claims-push.sh")],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        pass


HOOK_RE = __import__("re").compile(r"^\s*/(?:d|claim)(?:\s+(.*))?$", __import__("re").S)


def new_task(content: str) -> dict:
    """`/d new <task>`: create it in Todoist, due today, and return it. An
    `@code` token becomes its label (like /todo). dtd's cache refresh runs
    detached, so the row appears on the next watcher reload without making
    the prompt wait for it."""
    import re
    import subprocess
    lib = str(Path.home() / "i446-monorepo" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import todoist
    labels = re.findall(r"(?:^|\s)@(\S+)", content)
    content = re.sub(r"(?:^|\s)@\S+", "", content).strip()
    task = todoist.create_task(content, labels=labels or None, due_string="today")
    try:
        subprocess.Popen(["python3", str(Path(__file__).resolve().parent / "did-fast.py"),
                          "--refresh-cache"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass
    return task


VALUE_RE = __import__("re").compile(r"^\s*\[(\d+)\]\s*$")
_POINTS_RE = __import__("re").compile(r"\s*\[\d+\]")


def set_value(session: str, n: int, root: Path | None = None) -> str:
    """A bare `[N]` while this session holds a claim is the claimed task's
    VALUE (opportunity), not a completion: write it onto the Todoist task so
    dtd's ⌥↵ credits it when JM finishes. Never logs points (2026-10-05: a
    bare [200] got logged as +200 immediately)."""
    root = root or CLAIMS_DIR
    try:
        tid = (root / "by-session" / session).read_text().strip()
    except OSError:
        return ""   # no claim: leave the prompt to the model
    import subprocess
    lib = str(Path.home() / "i446-monorepo" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import todoist
    try:
        content = (todoist.get_task(tid) or {}).get("content", "")
        new = _POINTS_RE.sub("", content).rstrip() + f" [{n}]"
        todoist._request("POST", f"/tasks/{tid}", {"content": new})
    except Exception as e:
        return f"[claim hook] couldn't set [{n}] on the claimed task ({e}). Do NOT log points."
    try:
        subprocess.Popen(["python3", str(Path(__file__).resolve().parent / "did-fast.py"),
                          "--refresh-cache"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass
    return (f"[claim hook] set value [{n}] on the claimed task: {new}. This is the task's "
            "value (opportunity), NOT a completion: do NOT log points or run /did. "
            "JM credits it by completing the task in dtd (⌥↵).")


def hook(payload: str) -> str:
    """UserPromptSubmit fast path for `/d <task>[: request]` (and /claim):
    claim before the model even starts, so dtd shows it in ~1s instead of
    after the model has read the skill. Returns the line injected into the
    model's context ('' = not a claim prompt). The skill sees it and skips
    its own claim call."""
    try:
        d = json.loads(payload)
    except ValueError:
        return ""
    prompt = d.get("prompt") or ""
    session = d.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    v = VALUE_RE.match(prompt)
    if v and session:
        return set_value(session, int(v.group(1)))
    m = HOOK_RE.match(prompt)
    if not m or not session:
        return ""
    arg = (m.group(1) or "").strip()
    query = arg.split(":", 1)[0].strip()
    if query.lower() in ("off", "release", "done"):
        release(session)
        push()
        return "[claim hook] released this session's claim."
    if not query:
        return ""
    if query.lower().startswith("new "):
        name = query[4:].strip()
        if not name:
            return ""
        try:
            task = new_task(name)
        except Exception as e:
            return f"[claim hook] couldn't create {name!r} in Todoist ({e}); nothing claimed."
        rec = claim(task, session)
        push()
        return (f"[claim hook] created and 😈 claimed: {rec['task']} (id {rec['task_id']}). "
                "Already done; don't create or claim it again.")
    df = _did()
    cands = candidates(query, all_tasks(df.load_task_queue()), df)
    if not cands:
        return f"[claim hook] no dtd task matches {query!r}; nothing claimed."
    if len(cands) > 1:
        opts = "; ".join(f"{t['id']}: {t.get('content', '')}" for t in cands[:5])
        return f"[claim hook] ambiguous, nothing claimed. Candidates: {opts}"
    rec = claim(cands[0], session)
    push()
    return f"[claim hook] 😈 claimed: {rec['task']} (id {rec['task_id']}). Already done; don't run agent_claims.py claim again."


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], " ".join(argv[1:]).strip()
    if cmd == "hook":
        out = hook(sys.stdin.read())
        if out:
            print(out)
        return 0
    session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if cmd == "list":
        print(json.dumps(load(), ensure_ascii=False, indent=2))
        return 0
    if not session:
        print(json.dumps({"ok": False, "error": "no CLAUDE_CODE_SESSION_ID; run inside Claude"}))
        return 1
    if cmd == "release":
        print(json.dumps({"ok": True, "released": release(session)}))
        push()
        return 0
    if cmd == "new" and rest:
        rec = claim(new_task(rest), session)
        push()
        print(json.dumps({"ok": True, **rec}, ensure_ascii=False))
        return 0
    if cmd != "claim" or not rest:
        print(json.dumps({"ok": False, "error": "usage: claim <query> | release | list"}))
        return 1
    df = _did()
    tasks = all_tasks(df.load_task_queue())
    cands = candidates(rest, tasks, df)
    if not cands:
        print(json.dumps({"ok": False, "error": f"no task matches {rest!r}"}, ensure_ascii=False))
        return 2
    if len(cands) > 1:
        print(json.dumps({"ok": False, "ambiguous": [
            {"id": t["id"], "task": t.get("content", "")} for t in cands[:5]]},
            ensure_ascii=False))
        return 3
    rec = claim(cands[0], session)
    push()
    print(json.dumps({"ok": True, **rec}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
