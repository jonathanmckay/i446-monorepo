#!/usr/bin/env python3
"""done_session — /fin: finish this Claude session's claimed task.

    done_session.py            close it (JM's ⌥↵): points, Todoist, claim, timer
    done_session.py <N>        delay it N days (defer-fast --id, as dtd's ctrl-d)
    done_session.py <time>     hide it in dtd until that time TODAY (ctrl-v snooze
                               file, epoch float) — 1500 · 15:00 · 3pm · 3:30pm

The task: every claim of $CLAUDE_CODE_SESSION_ID that is held or marked ✅
(`agent_claims.py done` drops the id from the session index, so held() alone is
usually empty by the time JM types /fin; a 'released' claim is a task this
session let go of, never touched here).

Close: each task is completed exactly as dtd's ⌥↵ does it, `did-fast.py
--task-id <id> <content>`, which credits its points, closes Todoist, clears the
claim and stops the running Toggl timer only when the timer is that task.
Content comes from the dtd cache, not the claim record: a card renamed since
the claim would otherwise miss its timer.

Delay (days or time): nothing is credited. The claim is cleared with
agent_claims.clear_task (release() only drops held ids, so a ✅ claim would
otherwise show tomorrow's recurring card as finished for a day), and the Toggl
timer is stopped only if it is that task, via toggl_cli — did-fast is never
imported here (its module load talks to Excel, Todoist and ix-osa).
A bare integer is DAYS (`15` = 15 days); a time needs 4 digits, a colon or
am/pm (`1500`, `15:00`, `3pm`). The time path writes dtd's per-day snooze
file (same shape as ctrl-v +Nm: absolute epoch float, forward-only date) and
its Syncthing mirror, never a Todoist label — a 地支 label is durable and
would hide a recurring card at that hour every day.

Prints one JSON object: {"status": "done"|"deferred"|"delayed"|"no-claim"|
"failed", "tasks": [...]}. "done" means every task really closed (Todoist
included), so the conversation is safe to /clear.
"""
from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DID_FAST = HERE / "did-fast.py"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "jm"
TASK_QUEUE = STATE_DIR / "task-queue.json"
SNOOZE = STATE_DIR / "dtd-block-snooze.json"          # dtd ctrl-v snooze file (host-local)
SNOOZE_MIRROR_DIR = Path.home() / "vault" / "z_ibx"   # dtd-block-snooze-<host>.json, Syncthing-synced
DEFER_FAST = HERE / "defer-fast.py"
TOGGL_CLI = Path.home() / "i446-monorepo" / "mcp" / "toggl_server" / "toggl_cli.py"
ANNOT_RE = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")   # same as did-fast's


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


def parse_when(arg: str | None, now: _dt.datetime | None = None):
    """None → close. ("days", N) for a bare 1-3 digit integer (1..365).
    ("time", datetime today) for 1500 · 15:00 · 3pm · 3:30pm. Raises ValueError
    with a usage message otherwise (0 days, 2460, 13pm, 'tomorrow')."""
    if arg is None or not str(arg).strip():
        return None
    a = str(arg).strip().lower()
    now = now or _dt.datetime.now()
    if re.fullmatch(r"\d{1,3}", a):
        n = int(a)
        if not 1 <= n <= 365:
            raise ValueError(f"days must be 1..365, got {a}")
        return ("days", n)
    m = re.fullmatch(r"(\d{2})(\d{2})", a) or re.fullmatch(r"(\d{1,2}):(\d{2})", a)
    ampm = None
    if not m:
        m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", a)
        if m:
            ampm = m.group(3)
    if not m:
        raise ValueError(f"not a day count or a time: {arg!r} (use 3 · 1500 · 15:00 · 3pm)")
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if ampm:
        if not 1 <= h <= 12:
            raise ValueError(f"bad 12h hour: {arg!r}")
        h = h % 12 + (12 if ampm == "pm" else 0)
    if h > 23 or mi > 59:
        raise ValueError(f"bad time: {arg!r}")
    return ("time", now.replace(hour=h, minute=mi, second=0, microsecond=0))


def timer_key(s: str) -> str:
    """did-fast's _timer_key: no 😈, no (N)/[N]/{N}, single-spaced, lowercase."""
    return " ".join(ANNOT_RE.sub("", s.replace("😈", "")).split()).lower()


def stop_timer_if(content: str, run=subprocess.run, tid: str | None = None,
                  task_for_entry=None) -> str | None:
    """Stop the running Toggl timer only when it is this task (as did-fast's
    stop_matching_toggl, without importing did-fast). Returns a display line.
    By id first (2026-11-14): when the running entry is linked to a task
    (agent_claims.task_for_entry) it must be `tid`; unlinked → by name."""
    try:
        cur = run(["python3", str(TOGGL_CLI), "current"], capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001
        return None
    line = (cur.stdout or "").strip()
    if cur.returncode != 0 or not line.startswith("Running:"):
        return None
    desc = re.sub(r"^\d{2}:\d{2}-running\s+", "", line[len("Running:"):].strip())
    desc = desc[:desc.find(" @")].strip() if " @" in desc else desc.split("(")[0].strip()
    linked = None
    rid = re.search(r"\[id:(\d+)\]", line)
    if rid and tid and task_for_entry:
        try:
            linked = task_for_entry(rid.group(1))
        except Exception:  # noqa: BLE001
            linked = None
    if linked:
        if str(linked) != str(tid):
            return None
    elif not timer_key(desc) or timer_key(desc) != timer_key(content):
        return None
    try:
        st = run(["python3", str(TOGGL_CLI), "stop"], capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001
        return f"⏱ {desc} (stop failed)"
    return f"⏱ {desc} stopped" if st.returncode == 0 else f"⏱ {desc} (stop failed)"


def snooze_until(ids: list[str], until: _dt.datetime, path: Path = SNOOZE,
                 mirror_dir: Path = SNOOZE_MIRROR_DIR, now: _dt.datetime | None = None) -> float:
    """Hide ids in dtd until `until` today: dtd.sh's ctrl-v writer, minute form
    (absolute epoch float; an int would mean an hour-of-day block delay).
    Forward-only date gate and the per-host vault mirror exactly as dtd.sh
    (DTD_BLOCKAPPLY) does them, so dtd cli, dtd web and the other host all
    read it. Returns the epoch written."""
    now = now or _dt.datetime.now()
    if until <= now:
        raise ValueError(f"{until.strftime('%H:%M')} has already passed")
    today = now.date().isoformat()
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or today > data.get("date", ""):
            data = {}
    except (OSError, ValueError):
        data = {}
    if today > data.get("date", ""):
        data["date"] = today
    sn = data.setdefault("snoozes", {})
    epoch = float(until.timestamp())
    for i in ids:
        sn[str(i)] = epoch
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)
    try:
        slug = re.sub(r"[^a-z0-9]+", "-", socket.gethostname().lower()).strip("-")[:24]
        mirror_dir.mkdir(parents=True, exist_ok=True)
        mp = mirror_dir / f"dtd-block-snooze-{slug}.json"
        mt = mp.with_name(mp.name + ".tmp")
        mt.write_text(json.dumps(data))
        os.replace(mt, mp)
    except OSError:
        pass
    return epoch


def defer_days(tid: str, days: int, run=subprocess.run) -> tuple[bool, str]:
    """dtd's ctrl-d path: defer-fast --id <tid> <days>, no points argument (the
    posthoc stub is a [0] audit record, not credit). (ok, display)."""
    p = run(["python3", str(DEFER_FAST), "--id", tid, str(days)], capture_output=True, text=True, timeout=60)
    try:
        out = json.loads(p.stdout or "{}")
    except ValueError:
        out = {}
    if p.returncode != 0 or out.get("error"):
        return False, out.get("error") or (p.stderr or p.stdout or "").strip()[-160:] or f"defer-fast exit {p.returncode}"
    return True, f"→ {out.get('target_date', '?')}"


FIN_CLOSE_DIR = STATE_DIR / "fin-close"


def mark_tab_close(session: str, env=os.environ, run=subprocess.run) -> bool:
    """/fin closes the cmux tab (2026-11-14): when this thread's tab is driven from another
    host (claude-tracked exports CC_ORIGIN into the tmux session), leave fin-close/<tmux
    session> = <session id>. The Ix launcher reads it when claude exits (JM's `/exit`) and
    lets the tmux session end; the Straylight launcher then closes the tab or starts a fresh
    thread in it (see claude-tracked, after_ix_exit). Local/phone sessions: nothing."""
    origin = (env.get("CC_ORIGIN") or "").lower()
    if not origin or origin == socket.gethostname().split(".")[0].lower():
        return False
    pane = env.get("TMUX_PANE")
    if not pane or not env.get("TMUX"):
        return False
    try:
        name = run(["tmux", "display-message", "-p", "-t", pane, "#S"],
                   capture_output=True, text=True, timeout=2).stdout.strip()
    except Exception:  # noqa: BLE001
        return False
    if not name:
        return False
    FIN_CLOSE_DIR.mkdir(parents=True, exist_ok=True)
    (FIN_CLOSE_DIR / name).write_text(session + "\n")
    return True


def main() -> int:
    session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not session:
        print(json.dumps({"status": "failed", "error": "no CLAUDE_CODE_SESSION_ID; run inside Claude"}))
        return 1
    try:
        when = parse_when(sys.argv[1] if len(sys.argv) > 1 else None)
    except ValueError as e:
        print(json.dumps({"status": "failed", "error": str(e), "tasks": []}))
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
    # Final report per task (fin_report.py), gathered BEFORE the close: the
    # claim (its claimed_at and linked timer) is gone afterwards. Credited
    # points only on a close; a defer/snooze earns nothing.
    fr = _load("fin_report")
    finals = {}
    # Every claim this session made (released ones too) bounds the window of
    # the task claimed before it, so reports partition the session.
    starts = [r.get("claimed_at") for r in claims.values() if r.get("session") == session]
    for tid in ids:
        rec = claims.get(tid) or {}
        since = float(rec.get("claimed_at") or 0)
        try:
            finals[tid] = fr.build(session, tid, current_content(tid, claims, tasks),
                                   since, credited=when is None,
                                   linked_entry_id=rec.get("toggl_entry_id"),
                                   until=fr.window_end(since, starts) if since else None)
        except Exception as e:  # noqa: BLE001 — the report never blocks the close
            finals[tid] = {"error": str(e)}
    if when is None:
        for tid in ids:
            content = current_content(tid, claims, tasks)
            p = subprocess.run(["python3", str(DID_FAST), "--task-id", tid, content],
                               capture_output=True, text=True)
            ok, display = closed_ok(p.returncode, p.stdout, tid, content, outcome)
            all_ok &= ok
            report.append({"id": tid, "task": content, "ok": ok, "result": display,
                           "timer": timer_line(p.stdout), "report": finals.get(tid)})
        tab_close = False
        if all_ok:
            ac.set_free(session)   # tab reads ~~ until something new starts
            ac._retitle(session)
            tab_close = mark_tab_close(session)
        print(json.dumps({"status": "done" if all_ok else "failed", "tasks": report,
                          "tab_close": tab_close}, ensure_ascii=False, indent=2))
        return 0 if all_ok else 2
    kind, val = when
    if kind == "time":
        try:
            snooze_until(ids, val)
        except ValueError as e:
            print(json.dumps({"status": "failed", "error": str(e), "tasks": []}))
            return 1
    for tid in ids:
        content = current_content(tid, claims, tasks)
        if kind == "days":
            ok, display = defer_days(tid, val)
        else:
            ok, display = True, f"hidden until {val.strftime('%H:%M')} today"
        all_ok &= ok
        if ok:
            ac.clear_task(tid)   # not release(): a ✅ claim must not outlive the delay
        report.append({"id": tid, "task": content, "ok": ok, "result": display,
                       "timer": stop_timer_if(content, tid=tid, task_for_entry=ac.task_for_entry) if ok else None,
                       "report": finals.get(tid)})
    status = ("deferred" if kind == "days" else "delayed") if all_ok else "failed"
    tab_close = False
    if all_ok:
        ac.set_free(session)
        ac._retitle(session)
        tab_close = mark_tab_close(session)
    print(json.dumps({"status": status, "tasks": report, "tab_close": tab_close},
                     ensure_ascii=False, indent=2))
    return 0 if all_ok else 2


if __name__ == "__main__":
    sys.exit(main())
