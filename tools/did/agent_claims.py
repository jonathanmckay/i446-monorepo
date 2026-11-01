#!/usr/bin/env python3
"""agent_claims — tie a dtd task to the Claude session working on it.

/claim <task>[: prompt] links the CURRENT Claude session to a task (2026-10-05).
While that session is mid-turn, dtd greys the task out with 😈 and its top
line shows a spinner; when the turn ends the task looks normal again.

Store: ~/vault/z_ibx/agent-claims/ (Syncthing carries it to Ix, where dtd runs)
  <task_id>.json          {task_id, task, session, host, cwd, claimed_at}
  <task_id>.state         "working <epoch>" | "idle <epoch>" | "released <epoch>" | "done <epoch>"
  by-session/<session>    the task_id(s) this session holds, one per line (a
                          plain claim holds one; `claim --also` adds more,
                          2026-10-22, for work fanned out to subagents)
  panes.json              {session: n} the session's cmux number (dtd 1, janus 2,
                          Claude panes 3+), written by cs.py's cmux_panes() on
                          every prompt/stop via the session-title hook; load()
                          attaches it as 'pane' so dtd can draw "3😈" (2026-10-21)

Writers: this CLI (claim/release) and agent-claim-hook.sh (UserPromptSubmit →
working, Stop → idle, SessionEnd → release). Every write is tmp + rename so the
directory mtime advances; dtd's watcher reloads on that.

Usage:
  agent_claims.py claim <query>   fuzzy-match a task in the dtd cache, claim it
  agent_claims.py hook            UserPromptSubmit fast path for `/d <task>: ...`
  agent_claims.py new <task>      create a today task and claim it
  agent_claims.py claim --also <query>   claim another task WITHOUT releasing this session's others
  agent_claims.py done [<query>]  the agent thinks it's finished: ✅ on the left in dtd
                                  (default: every task this session holds), until JM completes it
  agent_claims.py release         hand this session's task back (stays at the top of dtd, 🕐)
  agent_claims.py list            JSON of claims (+ derived working flag)
  agent_claims.py note            Stop hook: post a two-sentence 😈 summary of the turn
                                  as a Todoist comment on each task the session holds
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
# A released claim keeps the task at the TOP of dtd (2026-10-21, "when an
# agent releases a task, it should remain at the top"): the record stays with
# state 'released' so the list generator still sorts it into the claimed tier
# (🕐, waiting on JM) until JM completes or defers it. Pruned after this long.
RELEASED_TTL = 86400


def _dirs(root: Path) -> list[Path]:
    """The store's root (claims made on this host) plus remote/<host>/, where
    each other host mirrors its own store (scripts/agent-claims-push.sh). The
    push used to rsync --delete into the root and wiped Ix-local claims on
    every Straylight hook fire (2026-10-25); now every reader merges these."""
    try:
        return [root] + sorted(d for d in (root / "remote").iterdir() if d.is_dir())
    except OSError:
        return [root]


# --- store ---------------------------------------------------------------
def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def set_state(task_id: str, state: str, now: float | None = None,
              root: Path = CLAIMS_DIR) -> None:
    _write(root / f"{task_id}.state", f"{state} {int(now or time.time())}\n")


def held(session: str, root: Path = CLAIMS_DIR) -> list[str]:
    """Task ids this session holds (the by-session index, one per line)."""
    try:
        return [l.strip() for l in (root / "by-session" / session).read_text().splitlines() if l.strip()]
    except OSError:
        return []


def _set_held(session: str, ids: list[str], root: Path) -> None:
    idx = root / "by-session" / session
    if ids:
        _write(idx, "".join(i + "\n" for i in dict.fromkeys(ids)))
    else:
        try:
            idx.unlink()
        except OSError:
            pass


def release(session: str, root: Path = CLAIMS_DIR, now: float | None = None) -> str | None:
    """Hand the task(s) back to JM: the session index goes, each claim record
    stays as 'released' so dtd keeps the row at the top (see RELEASED_TTL).
    Returns the first id released."""
    ids = held(session, root)
    if not ids:
        return None
    for tid in ids:
        if (root / f"{tid}.json").exists():
            set_state(tid, "released", now, root)
        else:
            try:
                (root / f"{tid}.state").unlink()
            except OSError:
                pass
    _set_held(session, [], root)
    return ids[0]


def mark_done(session: str, ids: list[str] | None = None, root: Path = CLAIMS_DIR,
              now: float | None = None) -> list[str]:
    """The agent believes the task is finished (2026-10-22: "if the agent or
    subagent thinks it is done with a claimed task, it should put a checkmark
    emoji on the left"). State 'done' → dtd draws ✅ on the left and keeps the
    row in the claimed tier until JM completes it (his ⌥↵ is the real close;
    pruned after RELEASED_TTL like a release). The id leaves the session index
    so later turns' hooks don't flip it back to working/idle."""
    mine = held(session, root)
    targets = ids if ids else mine
    for tid in targets:
        if (root / f"{tid}.json").exists():
            set_state(tid, "done", now, root)
    _set_held(session, [i for i in mine if i not in targets], root)
    # The turn that finishes a task is the one most worth a note, but the id
    # just left the session index; queue it so this turn's Stop still notes it.
    pend = root / "by-session" / f"{session}.note"
    try:
        old = pend.read_text().split()
    except OSError:
        old = []
    _write(pend, "".join(i + "\n" for i in dict.fromkeys(old + list(targets))))
    return targets


def clear_task(tid: str, root: Path = CLAIMS_DIR) -> bool:
    """The task was completed (did-fast closed it): drop its claim, whatever its
    state. JM's close is the real close, so nothing about the claim is left to
    show. Without this a REPEATING card kept its id, and the next day's card
    inherited yesterday's released claim as 🧮 🕐 (2026-10-24: "notes had the
    abacus as if I'm working on it, but it was long since stale"). Returns
    whether a claim existed."""
    existed = False
    for d in _dirs(root):
        existed |= (d / f"{tid}.json").exists()
        _forget(str(tid), d)
    return existed


def _forget(tid: str, root: Path) -> None:
    for p in (root / f"{tid}.json", root / f"{tid}.state"):
        try:
            p.unlink()
        except OSError:
            pass


def claim(task: dict, session: str, root: Path = CLAIMS_DIR,
          now: float | None = None, also: bool = False) -> dict:
    """One task per session: an earlier claim by this session is released —
    unless `also` (2026-10-22: one session fanning several tasks out to
    subagents), which adds this task to the ones the session already holds."""
    if not also:
        release(session, root)
    tid = str(task["id"])
    rec = {"task_id": tid, "task": task.get("content", ""), "session": session,
           "host": socket.gethostname().split(".")[0], "cwd": os.getcwd(),
           "claimed_at": int(now or time.time())}
    _write(root / f"{tid}.json", json.dumps(rec, ensure_ascii=False) + "\n")
    set_state(tid, "working", now, root)   # /claim itself runs mid-turn
    _set_held(session, (held(session, root) if also else []) + [tid], root)
    return rec


def pane_numbers(root: Path = CLAIMS_DIR) -> dict[str, int]:
    """session -> cmux pane number, from panes.json (empty when cs never wrote it).
    A remote host's map wins: cmux runs there, so its map is the live one."""
    out: dict[str, int] = {}
    for root_dir in _dirs(root):
        try:
            d = json.loads((root_dir / "panes.json").read_text())
            if isinstance(d, dict):
                out.update({str(k): int(v) for k, v in d.items()})
        except (OSError, ValueError, TypeError):
            pass
    return out


def load(root: Path = CLAIMS_DIR, now: float | None = None) -> dict[str, dict]:
    """task_id -> claim record with 'state', derived 'working', and 'pane' (the
    session's cmux number, or None when unknown)."""
    now = now or time.time()
    out: dict[str, dict] = {}
    files = []
    for d in _dirs(root):
        try:
            files += [f for f in d.glob("*.json") if f.name != "panes.json"]
        except OSError:
            pass
    panes = pane_numbers(root)
    for f in files:
        here = f.parent
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        tid = str(rec.get("task_id") or f.stem)
        state, ts = "idle", 0
        try:
            s, t = (here / f"{tid}.state").read_text().split()[:2]
            state, ts = s, int(t)
        except (OSError, ValueError):
            pass
        if state in ("released", "done") and now - ts > RELEASED_TTL:
            _forget(tid, here)   # kept its top slot for a day; JM never closed it
            continue
        if tid in out and out[tid]["state_ts"] >= ts:
            continue   # the same card claimed on two hosts: the newer state wins
        rec["state"] = state
        rec["state_ts"] = ts
        rec["working"] = state == "working" and now - ts < STALE_SECS
        # A released claim's session is gone or elsewhere: no pane number. A
        # done one keeps it while its session is still numbered, so dtd draws
        # "6✅" and JM can see which tab finished it (2026-10-22).
        rec["done"] = state == "done"
        rec["pane"] = None if state == "released" else panes.get(str(rec.get("session", "")))
        out[tid] = rec
    return out


def working_ids(root: Path = CLAIMS_DIR, now: float | None = None) -> set[str]:
    return {tid for tid, r in load(root, now).items() if r["working"]}


# --- turn notes (2026-10-25) -----------------------------------------------
# "when the agent finishes a turn on a claimed task, give a two sentence
# summary from the perspective of the LLM (with demon icon) in task notes, so I
# have more context." The Stop hook runs `note` detached; it reads the turn out
# of the transcript, has Haiku write two first-person sentences, and posts them
# as a Todoist comment ("notes" in Todoist's API) prefixed 😈. dtd's details
# screen shows the newest comments.
NOTE_PREFIX = "😈 "
NOTE_KEY_FILE = Path.home() / ".config" / "anthropic" / "key"
NOTE_MODEL = "claude-haiku-4-5-20251001"
NOTE_MAX_CHARS = 6000   # of turn text handed to the summarizer


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or []
                     if isinstance(b, dict) and b.get("type") == "text")


def turn_text(transcript: Path) -> tuple[str, str, list[str]]:
    """(prompt, reply, tools) for the last turn: the last real user prompt,
    every assistant text block after it, and the tools it called."""
    try:
        lines = transcript.read_text(errors="replace").splitlines()
    except OSError:
        return "", "", []
    entries = []
    for l in lines:
        try:
            e = json.loads(l)
        except ValueError:
            continue
        if e.get("type") in ("user", "assistant") and not e.get("isSidechain"):
            entries.append(e)
    start = None
    for i in range(len(entries) - 1, -1, -1):
        e = entries[i]
        c = (e.get("message") or {}).get("content")
        if e["type"] == "user" and not e.get("isMeta") and (
                isinstance(c, str) or any(isinstance(b, dict) and b.get("type") == "text" for b in c or [])):
            start = i
            break
    if start is None:
        return "", "", []
    prompt = _text_of(entries[start]["message"]["content"]).strip()
    reply, tools = [], []
    for e in entries[start + 1:]:
        if e["type"] != "assistant":
            continue
        for b in (e.get("message") or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                reply.append(b["text"].strip())
            elif b.get("type") == "tool_use":
                inp = b.get("input") or {}
                hint = inp.get("description") or inp.get("file_path") or ""
                tools.append(f"{b.get('name')}: {hint}"[:120])
    return prompt, "\n\n".join(reply), tools


def note_prompt(task: str, prompt: str, reply: str, tools: list[str]) -> str:
    body = (f"User's request:\n{prompt[:1500]}\n\nTools used:\n" + "\n".join(tools[-40:])
            + f"\n\nYour final reply:\n{reply}")[:NOTE_MAX_CHARS]
    return ("You are the AI coding agent that just finished a turn of work on the task "
            f"\"{task}\". Write exactly two sentences, first person (\"I ...\"), "
            "saying what you did and where it stands (done, blocked, or what is next). "
            "Concrete: name files, numbers or outcomes. No preamble, no quotes, under 60 words.\n\n"
            + body)


def summarize(text: str) -> str | None:
    """Haiku via the API (~1s), else `claude -p --model haiku` (thread_translator's pattern)."""
    out = None
    try:
        import anthropic
        resp = anthropic.Anthropic(api_key=NOTE_KEY_FILE.read_text().strip()).messages.create(
            model=NOTE_MODEL, max_tokens=200, messages=[{"role": "user", "content": text}])
        out = resp.content[0].text
    except Exception:  # noqa: BLE001
        import shutil
        import subprocess
        cli = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
        try:
            r = subprocess.run([cli, "-p", "--model", "haiku", text], capture_output=True,
                               text=True, timeout=120, cwd="/tmp",
                               env={**os.environ, "DTD_NOTE_CHILD": "1"})
            out = r.stdout if r.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            out = None
    out = " ".join((out or "").split()).strip().strip('"')
    if not out or "Not logged in" in out:
        return None
    return out


def note_targets(session: str, root: Path = CLAIMS_DIR) -> list[str]:
    """Held tasks plus any this turn marked done; consumes the done queue."""
    pend = root / "by-session" / f"{session}.note"
    try:
        queued = pend.read_text().split()
        pend.unlink()
    except OSError:
        queued = []
    return list(dict.fromkeys(held(session, root) + queued))


def note_turn(payload: str, root: Path = CLAIMS_DIR, post=None, summarizer=summarize) -> list[str]:
    try:
        j = json.loads(payload or "{}")
    except ValueError:
        return []
    session = j.get("session_id") or ""
    if not session or j.get("stop_hook_active"):
        return []
    tids = note_targets(session, root)
    if not tids:
        return []
    prompt, reply, tools = turn_text(Path(j.get("transcript_path") or "/nonexistent"))
    reply = (j.get("last_assistant_message") or "").strip() if not reply else reply
    if not reply and not tools:
        return []
    if post is None:
        sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
        import todoist
        post = todoist.add_comment
    claims = load(root)
    posted = []
    for tid in tids:
        task = (claims.get(tid) or {}).get("task", "")
        s = summarizer(note_prompt(task, prompt, reply, tools))
        if not s:
            continue
        try:
            post(tid, NOTE_PREFIX + s)
            posted.append(tid)
        except Exception:  # noqa: BLE001
            pass
    return posted


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


DTD_ORDER_FILE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "jm" / "dtd-order.json"
ROW_MAX_AGE = 6 * 3600   # an older render is not what JM is looking at


def row_task_id(n: int, path: Path = DTD_ORDER_FILE, now: float | None = None) -> str | None:
    """Task id on row n of dtd's latest render (1 = top), or None. The list
    generator writes the order on every render (2026-10-24), so "/d 5" means
    whatever row 5 shows now, not anything stored on the task."""
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    ids = d.get("ids") or []
    if (now or time.time()) - float(d.get("ts", 0)) > ROW_MAX_AGE or not 1 <= n <= len(ids):
        return None
    return str(ids[n - 1]) or None


def candidates(query: str, tasks: list[dict], df) -> list[dict]:
    """Ranked matches: a dtd row number, exact id, then substring of the
    cleaned name, then did-fast's word-overlap matcher."""
    q = query.strip().lower()
    if q.isdigit() and len(q) <= 3:
        # A row number never falls through to name matching: "5" would
        # otherwise fuzzy-match whatever task happens to contain a 5.
        tid = row_task_id(int(q))
        return [t for t in tasks if str(t["id"]) == tid] if tid else []
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
# `/d 21 can you look at this?` (2026-10-28): a row number with the request
# run on after it, no colon. A URL's `https:` is not the task/request colon.
ROW_PREFIX_RE = __import__("re").compile(r"^(\d{1,3})\s+\S")
TASK_COLON_RE = __import__("re").compile(r":(?!//)")

# dtd sections that are "surfaced" (visible task rows). The 0neon/1neon/夜neon
# sections are habit cards, never what a Claude thread is working on.
SURFACED_SECTIONS = ("关键路径", "today")
BARE_D_MAX = 40


def surfaced_tasks(tq: dict) -> list[dict]:
    seen, out = set(), []
    for sec in SURFACED_SECTIONS:
        for t in tq.get(sec) or []:
            if isinstance(t, dict) and t.get("id") and t["id"] not in seen:
                seen.add(t["id"])
                out.append(t)
    return out


def bare_d_line(tasks: list[dict], df, tq: dict | None = None) -> str:
    """Context line for a bare `/d`: the surfaced dtd tasks with ids, plus
    the two commands the model can finish with. `tasks` is the fallback when
    the queue has no surfaced sections (tests, odd caches)."""
    rows = surfaced_tasks(tq) if tq is not None else []
    if not rows:
        rows = tasks
    opts = "; ".join(f"{t['id']}: {t.get('content', '')}" for t in rows[:BARE_D_MAX])
    return ("[claim hook] bare /d, nothing claimed yet. Decide what this THREAD as a whole "
            "is producing (not the latest message), then either claim the surfaced dtd task "
            "that IS that work: `python3 ~/i446-monorepo/tools/did/agent_claims.py claim <id>` "
            "or, if none fits, create it: `python3 ~/i446-monorepo/tools/did/agent_claims.py "
            "new \"<thread goal> (N) [N] @code\"`. Surfaced: " + (opts or "(none)"))


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
# Skills that claim their own dtd card while they run, as if the prompt were
# `/d <task>`. Never creates: if the card is already gone (done), nothing is
# claimed. Skills in SKILL_RELEASES close the card themselves and then release
# the claim; the rest have a manual part (a survey, a form, a review JM
# finishes), so they keep the claim and JM completes the card in dtd.
# Keep scripts/agent-claim-hook.sh's prefilter in sync (a test enforces it);
# /1-i446's skill-claims check flags new skills that look like they belong here.
SKILL_CLAIMS = {
    "0t": "0t", "0g": "0g", "notes": "notes", "早餐": "早餐",
    "1s": "1s", "1g": "1g", "1s897": "1 s897", "1hcmc": "1 hcmc", "1hcb": "1 hcb",
    "1i9": "1 i9", "1m5x2": "1 m5x2", "xk887": "1 xk88", "1-1n": "1 -1n", "1-2g": "1 -2g",
    "f695": "1 f695", "2n": "2n checksum", "3s": "3s", "moralaudit": "moral audit",
    "2026-4g-checkin": "4g checkin", "1-i446": "1 i446", "1hcme": "1 hcme",
    "1f693": "1 f693", "ibxi9": "ibx i9", "ibxm5x2": "ibx m5x2",
}
# Skills whose flow closes the card and then releases the claim itself.
# (/xk887's survey closes '1 xk88' from its own cmux tab after the skill's
# turn is over, so the skill can't release; it stays a keeper.)
SKILL_RELEASES = {"0t", "notes", "0g", "早餐", "1i9", "1m5x2", "1hcme", "1hcmc", "1-2g"}
# Skills JM decided NOT to link (2026-10-11), so /1-i446 stops asking.
SKILL_NO_CLAIM = {
    "s": "/salat alias; the '1s' card is the weekly review",
    "s897": "people-db editor; the 's897' / '1 s897' cards are social time and the weekly review",
    "mealplan": "only the planning half of '1 groceries'",
    "ibx0": "closes 8 inbox cards; a session holds one claim",
    "ate": "/早餐 already covers the 早餐 card",
    "inkwelltodo": "files and claims one task per note item itself",
}
SKILL_RE = __import__("re").compile(
    r"^\s*/(" + "|".join(map(__import__("re").escape, SKILL_CLAIMS)) + r")(?:\s.*)?$", __import__("re").S)
_POINTS_RE = __import__("re").compile(r"\s*\[\d+\]")


def set_value(session: str, n: int, root: Path | None = None) -> str:
    """A bare `[N]` while this session holds a claim is the claimed task's
    VALUE (opportunity), not a completion: write it onto the Todoist task so
    dtd's ⌥↵ credits it when JM finishes. Never logs points (2026-10-05: a
    bare [200] got logged as +200 immediately)."""
    root = root or CLAIMS_DIR
    try:
        tid = held(session, root)[0]
    except IndexError:
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


_CMD_NAME_RE = __import__("re").compile(r"<command-name>\s*(/[^<\s]+)\s*</command-name>")
_CMD_ARGS_RE = __import__("re").compile(r"<command-args>(.*?)</command-args>", __import__("re").S)


def _unwrap_command(prompt: str) -> str:
    """A slash command can reach UserPromptSubmit wrapped as
    `<command-message>..</command-message><command-name>/notes</command-name>
    <command-args>..</command-args>` rather than the typed `/notes ...`
    (2026-10-07: `/notes` ran without claiming its card). Rebuild the typed
    form so HOOK_RE / SKILL_RE see `/name args`."""
    m = _CMD_NAME_RE.search(prompt)
    if not m:
        return prompt
    a = _CMD_ARGS_RE.search(prompt)
    args = (a.group(1).strip() if a else "")
    return f"{m.group(1)} {args}".rstrip()


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
    prompt = _unwrap_command(d.get("prompt") or "")
    session = d.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    v = VALUE_RE.match(prompt)
    if v and session:
        return set_value(session, int(v.group(1)))
    sk = SKILL_RE.match(prompt)
    if sk and session:
        # Exact card name only: no substring / word-overlap fallback, so a
        # weekly or quarterly card that isn't on today's list claims nothing
        # rather than some look-alike (2026-10-11).
        df = _did()
        want = SKILL_CLAIMS[sk.group(1)].lower()
        cands = [t for t in all_tasks(df.load_task_queue())
                 if " ".join(df.ANNOT_RE.sub("", t.get("content", "")).split()).lower() == want]
        if len(cands) != 1:
            return ""
        rec = claim(cands[0], session)
        push()
        tail = ("the skill releases the claim when it closes the card." if sk.group(1) in SKILL_RELEASES
                else "it has a manual part: leave the card open and the claim held; JM completes it in dtd.")
        return f"[claim hook] 😈 claimed: {rec['task']} (id {rec['task_id']}) for /{sk.group(1)}; {tail}"
    m = HOOK_RE.match(prompt)
    if not m or not session:
        return ""
    arg = (m.group(1) or "").strip()
    # The task is the first line, up to a colon. Anything after (a `:` or a
    # newline) is the request: `/d work offline sync` + a pasted /bug
    # paragraph used to become one long query that matched nothing and was
    # created as a new task (2026-10-22).
    query = TASK_COLON_RE.split(arg.split("\n", 1)[0], 1)[0].strip()
    if query.lower() == "done":
        # SKILL.md contract: `/d done` marks held tasks ✅ (agent-finished);
        # routing it to release() emptied the index first, so the ✅ and
        # that turn's 😈 note never happened (v169 review).
        mark_done(session)
        push()
        return "[claim hook] marked this session's claimed tasks ✅ (agent-finished)."
    if query.lower() in ("off", "release"):
        release(session)
        push()
        return "[claim hook] released this session's claim."
    df = _did()
    if not query:
        # Bare `/d` (2026-10-19): the task is whatever this THREAD is
        # accomplishing, which only the model knows. Hand it the surfaced
        # list with ids so it can pick (one claim call) or create (one new
        # call) without a second round trip; claim nothing here.
        tq = df.load_task_queue()
        return bare_d_line(all_tasks(tq), df, tq)
    explicit_new = query.lower().startswith("new ")
    tasks = [] if explicit_new else all_tasks(df.load_task_queue())
    row = None if explicit_new else ROW_PREFIX_RE.match(query)
    if row and not any(query.lower() in df.ANNOT_RE.sub("", t.get("content", "")).lower() for t in tasks):
        # A leading number is the dtd row unless the whole line names a
        # task ("1 hpm"); the rest is the request, never a new task's name.
        query = row.group(1)
    cands = [] if explicit_new else candidates(query, tasks, df)
    if not explicit_new and not cands and query.isdigit() and len(query) <= 3:
        # "/d 5" with no row 5 in a fresh render: never create a task named "5".
        return (f"[claim hook] no dtd row {query} in the latest render (dtd closed, "
                "or the list is shorter); nothing claimed.")
    if explicit_new or not cands:
        # No dtd match = new work: create it and claim (default since
        # 2026-10-06, "that should be the default behavior"). `/d new` stays
        # as the explicit form for a name that WOULD match something old.
        name = query[4:].strip() if explicit_new else query
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
    if cmd == "note":
        note_turn(sys.stdin.read())
        return 0
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
    also = False
    if cmd == "claim" and rest.startswith("--also "):
        also, rest = True, rest[len("--also "):].strip()
    if cmd == "done":
        ids = None
        if rest:
            if rest in load():
                ids = [rest]
            else:
                df = _did()
                c = candidates(rest, all_tasks(df.load_task_queue()), df)
                if len(c) != 1:
                    print(json.dumps({"ok": False, "error": f"{len(c)} tasks match {rest!r}"}, ensure_ascii=False))
                    return 2
                ids = [str(c[0]["id"])]
        done = mark_done(session, ids)
        push()
        print(json.dumps({"ok": True, "done": done}))
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
    rec = claim(cands[0], session, also=also)
    push()
    print(json.dumps({"ok": True, **rec}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
