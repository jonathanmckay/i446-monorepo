#!/usr/bin/env python3
"""
meeting-daemon.py — Auto-start/stop meeting recordings based on Outlook calendar.

Polls Outlook via AppleScript every 60s. When a meeting starts, launches meet.py.
When the meeting ends, sends SIGTERM to stop recording + transcribe.

Transcripts are saved as .wav + .txt. Filing to vault is handled separately
by Claude Code (manually or via scheduled agent).

Prerequisites:
    - Microsoft Outlook must be running
    - meet.py must be in the same directory
    - BlackHole audio device for system audio capture

Usage:
    python3 meeting-daemon.py              # run in foreground
    nohup python3 meeting-daemon.py &      # run in background

State: ~/.claude/skills/d357/state.json (shared with /d357 skill)
Log:   /tmp/meeting-daemon.log
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

POLL_INTERVAL = 60  # seconds
MEETING_DIR = Path(__file__).parent
STATE_FILE = Path.home() / ".claude/skills/d357/state.json"
LOG_FILE = Path("/tmp/meeting-daemon.log")

# Events to ignore (all-day events, focus time, etc.)
IGNORE_PATTERNS = [
    "focus time",
    "lunch",
    "block",
    "no meeting",
    "ooo",
    "out of office",
    "busy",
]


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def get_current_meeting() -> str | None:
    """Query Outlook for a meeting happening right now. Returns subject or None."""
    script = '''
    tell application "Microsoft Outlook"
        set now to current date
        set fiveAgo to now - 120
        set fiveAhead to now + 120
        set evts to every calendar event whose start time ≥ fiveAgo and start time ≤ now and end time ≥ now
        if (count of evts) = 0 then return ""
        -- Return the first (most recent) event
        set e to item 1 of evts
        return (subject of e)
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10
        )
        title = result.stdout.strip()
        if not title:
            return None
        # Filter out non-meetings
        if any(p in title.lower() for p in IGNORE_PATTERNS):
            return None
        return title
    except Exception as e:
        log(f"Outlook query failed: {e}")
        return None


def read_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"pid": None, "name": None, "started": None, "log": None}


def write_state(pid, name, log_path):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({
        "pid": pid,
        "name": name,
        "started": datetime.now().isoformat(timespec="seconds"),
        "log": str(log_path),
    }, indent=2))


def clear_state():
    STATE_FILE.write_text(json.dumps({
        "pid": None, "name": None, "started": None, "log": None
    }))


def is_recording(state: dict) -> bool:
    pid = state.get("pid")
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


AUDIO_SWITCH = Path(__file__).parent.parent.parent / "scripts" / "audio-switch.sh"


def start_recording(meeting_name: str):
    # Switch audio output to "Meet Output" so BlackHole captures call audio
    if AUDIO_SWITCH.exists():
        try:
            subprocess.run(["bash", str(AUDIO_SWITCH), "meet"],
                           capture_output=True, timeout=5)
            log("🔊 Audio switched to Meet Output")
        except Exception as e:
            log(f"⚠ Audio switch failed: {e}")

    log_path = f"/tmp/d357-daemon-{os.getpid()}.log"
    proc = subprocess.Popen(
        ["python3", str(MEETING_DIR / "meet.py"), meeting_name,
         "--domain", "d357", "--teams"],
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
        cwd=str(MEETING_DIR),
    )
    write_state(proc.pid, meeting_name, log_path)
    log(f"▶ Started recording: {meeting_name} (pid {proc.pid})")


def stop_recording(state: dict):
    pid = state.get("pid")
    name = state.get("name", "?")
    if not pid:
        return
    try:
        os.kill(pid, signal.SIGTERM)
        log(f"⏹ Stopped: {name} (pid {pid}) — transcribing...")
        # Wait for transcription to finish (up to 5 min)
        for _ in range(150):
            try:
                os.kill(pid, 0)
                time.sleep(2)
            except OSError:
                break
        log(f"✓ Transcription done: {name}")
    except OSError:
        log(f"Process {pid} already dead")

    # Switch audio back to default speakers
    if AUDIO_SWITCH.exists():
        try:
            subprocess.run(["bash", str(AUDIO_SWITCH), "default"],
                           capture_output=True, timeout=5)
            log("🔊 Audio switched back to default")
        except Exception:
            pass
    clear_state()


def main():
    log("Meeting daemon started. Polling every 60s.")

    # Verify Outlook is running
    result = subprocess.run(["pgrep", "-x", "Microsoft Outlook"], capture_output=True)
    if result.returncode != 0:
        log("⚠ Outlook not running — launching...")
        subprocess.Popen(["open", "-a", "Microsoft Outlook"])
        time.sleep(10)

    running = False

    while True:
        try:
            state = read_state()
            recording = is_recording(state)
            current_meeting = get_current_meeting()

            if current_meeting and not recording:
                # Meeting started — begin recording
                start_recording(current_meeting)

            elif not current_meeting and recording:
                # Meeting ended — stop recording
                stop_recording(state)

            elif current_meeting and recording:
                # Still in the same (or different) meeting
                if current_meeting != state.get("name"):
                    # Different meeting — stop old, start new
                    log(f"Meeting changed: {state.get('name')} → {current_meeting}")
                    stop_recording(state)
                    time.sleep(2)
                    start_recording(current_meeting)

        except Exception as e:
            log(f"Error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Daemon stopped by user.")
        state = read_state()
        if is_recording(state):
            stop_recording(state)
