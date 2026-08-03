#!/usr/bin/env python3
"""d357_quick.py — headless mechanical start/stop for meeting recordings.

The janus-facing subset of the /d357 skill (user request 2026-08-02: "hitting
'enter' on a meeting will also kick off a d357 recording session"): liveness-
guarded state handling, simplified audio pre-flight, the exact tmux launch the
skill uses, and a patient stop that waits for meet.py to transcribe + file.

Deliberately NOT here (janus owns them, or the interactive /d357 skill does):
  - Toggl start/stop — janus's convert flow already runs the timer.
  - 0分 points — janus grants them via did-fast when it finalizes the meeting
    (writing them here too would double-credit).
  - Bluetooth bouncing — janus always starts at/inside the meeting, so the
    /d357 skill's MID-CALL GUARD applies unconditionally: never bounce; fall
    back to mic-only instead.
  - The enhanced Claude filing pass — meet.py's own extract+file runs on stop.

Protocol (stdout, single line, machine-parseable):
  start → "REC|<name>|<audio verdict or unconfirmed>"  (exit 0)
          "ERR|<reason>"                               (exit 1)
  stop  → "STOPPED|<name>|<txt path or ?>"             (exit 0)
          "NOACTIVE|"                                  (exit 0)
  status→ "ACTIVE|<name>|<started>" or "NOACTIVE|"
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

# Callers like janus run with a GUI-app PATH that lacks homebrew — tmux and
# SwitchAudioSource silently vanish and the start dies with no recording
# (2026-08-02: "I see no mic icon for strat"). Make the wrapper self-sufficient.
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")

STATE = Path.home() / ".local/state/jm/d357-state.json"
LOG = Path("/tmp/d357-active.log")
MEET_DIR = Path.home() / "i446-monorepo/tools/meet"
PROF = Path.home() / "i446-monorepo/tools/prof/log_arrival.py"
TMUX_SESSION = "d357"


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _write_state(data: dict) -> None:
    """Atomic (temp-file + rename) so a concurrent reader (janus's boot
    restore, meeting-daemon.py — same STATE file) never sees a truncated
    write mid-way through."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data))
    tmp.replace(STATE)


def _clear_state() -> None:
    try:
        _write_state({"pid": None})
    except OSError:
        pass


def _tmux_alive(session: str) -> bool:
    return subprocess.run(["tmux", "has-session", "-t", session],
                          capture_output=True).returncode == 0


def is_alive(state: dict) -> bool:
    """The /d357 skill's three-part liveness guard: pid exists AND the tmux
    session exists AND the recording started today. Anything less is a stale
    leftover (the 43-hour-zombie class, 2026-06-10) — never trust it."""
    pid = state.get("pid")
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    if not _tmux_alive(state.get("tmux") or TMUX_SESSION):
        return False
    started = str(state.get("started") or "")
    return started[:10] == date.today().isoformat()


def _hfp_mode() -> bool:
    """AirPods in HFP (1ch out @24kHz) silently break the Meet Output →
    BlackHole route; record mic-only instead of pretending."""
    try:
        import sounddevice as sd
        for d in sd.query_devices():
            if "AirPods" in d["name"] and d["max_output_channels"] > 0:
                if d["max_output_channels"] == 1 and int(d["default_samplerate"]) == 24000:
                    return True
    except Exception:
        pass
    return False


def build_tmux_command(name: str, mic_only: bool, log_path: Path, max_minutes: int = 0) -> str:
    """The /d357 skill's step-6 launch line, verbatim semantics: `>` redirect
    (never tee), mic-only forces --idle-timeout 0."""
    safe = name.replace("'", "")
    parts = [f"cd {MEET_DIR} && PYTHONUNBUFFERED=1 python3 -u meet.py '{safe}' --domain d357"]
    if mic_only:
        parts.append("--no-teams --idle-timeout 0")
    if max_minutes:
        parts.append(f"--max-duration {max_minutes}")
    parts.append(f"> {log_path} 2>&1")
    return " ".join(parts)


def parse_stop_log(text: str) -> str | None:
    """Transcript path from meet.py's log ('TXT → /path')."""
    m = re.search(r"TXT → (\S+)", text)
    return m.group(1) if m else None


def _prof(event: str, name: str) -> None:
    try:
        subprocess.run(["python3", str(PROF), event, "--name", name],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def _wait_for(pattern: str, timeout_s: int, log_path: Path, session: str) -> str | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            text = log_path.read_text()
        except OSError:
            text = ""
        m = re.search(pattern, text)
        if m:
            return m.group(0)
        if not _tmux_alive(session):
            return None
        time.sleep(2)
    return None


def cmd_stop() -> int:
    state = _load_state()
    if not is_alive(state):
        _clear_state()
        print("NOACTIVE|")
        return 0
    name = state.get("name") or "meeting"
    session = state.get("tmux") or TMUX_SESSION
    log_path = Path(state.get("log") or LOG)

    # Per-session exclusive claim: whoever wins sends the C-c, everyone else
    # (janus's normal detached spawn AND cmd_start's belt-and-suspenders
    # safety net both target the same session) no-ops instead of sending a
    # SECOND interrupt — which can land mid-artifact-write and lose the
    # meeting entirely (2026-08-02 incident: 2 of 3 back-to-back recordings
    # that day lost their transcripts this way). Session names are unique
    # per recording (see cmd_start), so this lock can never be reused/stale.
    lock_path = Path(f"/tmp/d357-stopping-{session}.lock")
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        print(f"STOPPED|{name}|(already stopping)")
        return 0

    try:
        subprocess.run(["tmux", "send-keys", "-t", session, "C-c"], capture_output=True)
        # meet.py needs time for the wav save + Whisper + note filing. One
        # C-c, then wait — never spam interrupts.
        _wait_for(r"Done!|TXT → ", 300, log_path, session)
        txt = None
        try:
            txt = parse_stop_log(log_path.read_text())
        except OSError:
            pass
        _prof("stop", name)
        # Only clear state if it's still THIS recording — cmd_start may have
        # already overwritten it with a new one while we were waiting.
        if _load_state().get("tmux") == session:
            _clear_state()
        print(f"STOPPED|{name}|{txt or '?'}")
        return 0
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def cmd_start(name: str, max_minutes: int = 0) -> int:
    old_state = _load_state()
    if is_alive(old_state) and old_state.get("source") == "janus":
        # Safety net for a desynced old recording (janus's in-memory view
        # lost track of it, e.g. across a crash/restart) — never for a
        # meeting-daemon.py-owned recording (source "daemon"), that's not
        # ours to touch. Fire-and-forget: cmd_stop()'s per-session lock
        # makes this a harmless no-op in the common case where janus's own
        # _spawn_d357_stop() already claimed it.
        try:
            subprocess.Popen([sys.executable, __file__, "stop"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        except Exception:
            pass

    # Unique per recording — never shared with a still-finishing old one, so
    # starting the new recording never has to wait on (or race) the old
    # one's stop/transcribe. ms timestamp + pid: two starts can't collide.
    session = f"d357-janus-{int(time.time() * 1000)}-{os.getpid()}"
    log_path = Path(f"/tmp/d357-janus-{int(time.time() * 1000)}-{os.getpid()}.log")

    mic_only = _hfp_mode()
    if not mic_only:
        r = subprocess.run(["SwitchAudioSource", "-s", "Meet Output"], capture_output=True)
        if r.returncode != 0:
            mic_only = True

    launch = build_tmux_command(name, mic_only, log_path, max_minutes)
    r = subprocess.run(["tmux", "new-session", "-d", "-s", session, launch],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"ERR|tmux launch failed: {(r.stderr or '').strip()[:80]}")
        return 1
    pid = ""
    try:
        pid = subprocess.run(["tmux", "list-panes", "-t", session, "-F", "#{pane_pid}"],
                             capture_output=True, text=True).stdout.strip().splitlines()[0]
    except Exception:
        pass
    _write_state({
        "pid": int(pid) if pid.isdigit() else None,
        "tmux": session, "name": name,
        "started": datetime.now().isoformat(timespec="seconds"),
        "log": str(log_path), "mic_only": mic_only, "source": "janus",
    })
    _prof("start", name)
    verdict = _wait_for(r"AUDIO_VERDICT [^\n]*", 25, log_path, session)
    if verdict is None and not _tmux_alive(session):
        _clear_state()
        tail = ""
        try:
            tail = log_path.read_text()[-120:].replace("\n", " ")
        except OSError:
            pass
        print(f"ERR|recording died at launch: {tail}")
        return 1
    print(f"REC|{name}|{(verdict or 'unconfirmed').replace('AUDIO_VERDICT ', '')}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] == "status":
        st = _load_state()
        if is_alive(st):
            print(f"ACTIVE|{st.get('name')}|{st.get('started')}")
        else:
            print("NOACTIVE|")
        return 0
    if args[0] == "stop":
        return cmd_stop()
    if args[0] == "start":
        if len(args) < 2:
            print("ERR|usage: d357_quick.py start <name> [--minutes N]")
            return 1
        mins = 0
        rest = args[1:]
        if "--minutes" in rest:
            i = rest.index("--minutes")
            mins = int(rest[i + 1])
            del rest[i:i + 2]
        return cmd_start(" ".join(rest), mins)
    print(f"ERR|unknown command {args[0]!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
