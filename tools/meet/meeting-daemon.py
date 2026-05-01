#!/usr/bin/env python3
"""
meeting-daemon.py — Auto-start/stop meeting recordings based on Outlook calendar.

Polls Outlook every POLL_INTERVAL seconds. When a calendar event matches the
"is a real meeting" filter chain AND a call client (Teams/Zoom) is active, shows
a 5-second cancel dialog, then launches meet.py + Toggl. Stops when the calendar
event ends or the call ends.

Filters applied (all must pass):
  1. Title not in denylist (focus, lunch, block, ooo, etc.)
  2. Not an all-day event
  3. >= 2 attendees
  4. Your RSVP is not declined
  5. Has Teams/Zoom/Meet link in body OR a non-empty location
  6. Teams or Zoom process is currently running

Prerequisites:
    - Microsoft Outlook running
    - meet.py in same directory
    - BlackHole / Multi-Output Device for system audio
    - terminal-notifier (brew install terminal-notifier) — optional

Usage:
    python3 meeting-daemon.py              # foreground
    nohup python3 meeting-daemon.py &      # background
    launchctl load ~/Library/LaunchAgents/com.mckay.meeting-daemon.plist

State: ~/.claude/skills/d357/state.json (shared with /d357 skill)
Log:   /tmp/meeting-daemon.log
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

POLL_INTERVAL = 60  # seconds
CANCEL_TIMEOUT = 5  # seconds
MEETING_DIR = Path(__file__).parent
STATE_FILE = Path.home() / ".claude/skills/d357/state.json"
PENDING_FILE = Path.home() / ".claude/skills/d357/pending-files.jsonl"
LOG_FILE = Path("/tmp/meeting-daemon.log")
TOGGL_CLI = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"
AUDIO_SWITCH = Path.home() / "i446-monorepo/scripts/audio-switch.sh"
DEFAULT_PROJECT = "m5x2"

IGNORE_PATTERNS = [
    "focus time", "focus block", "lunch", "block", "no meeting",
    "ooo", "oof", "out of office", "busy", "hold ", "tentative hold",
    "commute", "personal", "doctor",
]

CALL_PROCESSES = [
    "Microsoft Teams",
    "Microsoft Teams (work or school)",
    "MSTeams",
    "Teams",
    "zoom.us",
    "Google Chrome Helper",  # Meet runs in Chrome; weak signal — keep off by default
]
# Strong signal: Teams or Zoom only. Chrome Helper too noisy.
CALL_PROCESSES_STRONG = ["Microsoft Teams", "MSTeams", "Teams", "zoom.us"]

LINK_RE = re.compile(
    r"(teams\.microsoft\.com|zoom\.us/j/|meet\.google\.com|webex\.com|"
    r"chime\.aws|whereby\.com|meet\.jit\.si)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Outlook query
# --------------------------------------------------------------------------- #
OUTLOOK_SCRIPT = r'''
tell application "Microsoft Outlook"
    set now to current date
    set windowStart to now - 120
    set windowEnd to now + 60
    set evts to every calendar event whose start time >= windowStart and start time <= windowEnd and end time >= now
    if (count of evts) = 0 then return ""

    set e to item 1 of evts
    set theSubject to subject of e
    set theLocation to ""
    try
        set theLocation to location of e
    end try
    set theContent to ""
    try
        set theContent to plain text content of e
    end try
    set theAllDay to all day flag of e
    set theStart to start time of e
    set theEnd to end time of e
    set theStatus to "none"
    try
        set theStatus to (response status of e) as string
    end try
    set theAttendees to 0
    try
        set theAttendees to count of (every attendee of e)
    end try

    -- Format times as ISO-ish: YYYY-MM-DD HH:MM:SS
    set startStr to my fmt(theStart)
    set endStr to my fmt(theEnd)

    set AppleScript's text item delimiters to "||"
    return {theSubject, startStr, endStr, theAllDay as string, ¬
            theAttendees as string, theStatus, theLocation, theContent} as string
end tell

on fmt(d)
    set y to year of d as integer
    set m to (month of d as integer)
    set dy to day of d as integer
    set h to hours of d
    set mi to minutes of d
    set s to seconds of d
    return (y as string) & "-" & my pad(m) & "-" & my pad(dy) & " " & ¬
           my pad(h) & ":" & my pad(mi) & ":" & my pad(s)
end fmt

on pad(n)
    set s to n as string
    if (count s) = 1 then return "0" & s
    return s
end pad
'''


def get_current_meeting() -> dict | None:
    """Return dict with meeting fields, or None if no event in window."""
    try:
        result = subprocess.run(
            ["osascript", "-e", OUTLOOK_SCRIPT],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log(f"Outlook query failed: {e}")
        return None

    out = result.stdout.strip()
    if not out:
        return None

    parts = out.split("||")
    if len(parts) < 8:
        log(f"Unexpected Outlook output: {out!r}")
        return None

    subject, start, end, all_day, attendees, status, location, content = parts[:8]
    return {
        "subject": subject.strip(),
        "start": start.strip(),
        "end": end.strip(),
        "all_day": all_day.strip().lower() == "true",
        "attendees": int(attendees) if attendees.strip().isdigit() else 0,
        "response": status.strip().lower(),
        "location": location.strip(),
        "content": content.strip(),
    }


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
def should_record(m: dict) -> tuple[bool, str]:
    title = m["subject"].lower()
    if not title:
        return False, "no title"
    if any(p in title for p in IGNORE_PATTERNS):
        return False, f"title matches denylist ({title!r})"
    if m["all_day"]:
        return False, "all-day event"
    if m["attendees"] < 2:
        return False, f"only {m['attendees']} attendees"
    if "decline" in m["response"]:
        return False, f"RSVP={m['response']}"
    has_link = bool(LINK_RE.search(m["content"]))
    has_loc = bool(m["location"])
    if not has_link and not has_loc:
        return False, "no online-meeting link or location"
    return True, "ok"


def is_call_active() -> bool:
    """Return True if Teams or Zoom is currently running."""
    try:
        result = subprocess.run(
            ["pgrep", "-x"] + CALL_PROCESSES_STRONG,
            capture_output=True, timeout=3,
        )
        return result.returncode == 0
    except Exception:
        # Fall back: if pgrep fails, don't gate on it
        return True


# --------------------------------------------------------------------------- #
# Cancel notification
# --------------------------------------------------------------------------- #
def confirm_with_cancel(name: str) -> bool:
    """Show a 5-second dialog. Default = OK (record). Cancel = skip.

    Returns True if user did not cancel.
    """
    safe_name = name.replace('"', "'")
    script = f'''
    try
        display dialog "Auto-recording: {safe_name}\n\nWill start in {CANCEL_TIMEOUT}s. Click Skip to cancel." \
            buttons {{"Skip", "Record now"}} \
            default button "Record now" \
            cancel button "Skip" \
            with title "Meeting Daemon" \
            with icon note \
            giving up after {CANCEL_TIMEOUT}
        set r to result
        if button returned of r is "Skip" then
            return "cancel"
        end if
        return "ok"
    on error errMsg number errNum
        if errNum is -128 then return "cancel"
        return "ok"
    end try
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=CANCEL_TIMEOUT + 5,
        )
        return result.stdout.strip() != "cancel"
    except Exception as e:
        log(f"Cancel dialog failed (proceeding): {e}")
        return True


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def read_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"pid": None}


def write_state(d: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(d, indent=2))


def clear_state() -> None:
    write_state({"pid": None})


def is_recording(state: dict) -> bool:
    pid = state.get("pid")
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# Toggl
# --------------------------------------------------------------------------- #
def toggl_start(name: str, project: str = DEFAULT_PROJECT) -> int | None:
    try:
        result = subprocess.run(
            ["python3", str(TOGGL_CLI), "start", name, project],
            capture_output=True, text=True, timeout=15,
        )
        # toggl_cli.py prints "Started: <name> → <project> [id:NNN]"
        match = re.search(r"id[:\s]+(\d+)", result.stdout)
        if match:
            return int(match.group(1))
    except Exception as e:
        log(f"Toggl start failed: {e}")
    return None


def toggl_stop() -> None:
    try:
        subprocess.run(
            ["python3", str(TOGGL_CLI), "stop"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        log(f"Toggl stop failed: {e}")


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #
def audio_switch(mode: str) -> None:
    if not AUDIO_SWITCH.exists():
        return
    try:
        subprocess.run(["bash", str(AUDIO_SWITCH), mode],
                       capture_output=True, timeout=5)
    except Exception as e:
        log(f"Audio switch ({mode}) failed: {e}")


def calendar_minutes(meeting: dict) -> int | None:
    try:
        s = datetime.strptime(meeting["start"], "%Y-%m-%d %H:%M:%S")
        e = datetime.strptime(meeting["end"], "%Y-%m-%d %H:%M:%S")
        return max(1, int((e - s).total_seconds() / 60))
    except Exception:
        return None


def start_recording(meeting: dict) -> None:
    name = meeting["subject"]
    audio_switch("meet")
    log_path = f"/tmp/d357-daemon-{int(time.time())}.log"
    cmd = ["python3", str(MEETING_DIR / "meet.py"), name,
           "--domain", "d357", "--teams"]
    minutes = calendar_minutes(meeting)
    if minutes:
        cmd.extend(["--max-duration", str(minutes)])

    proc = subprocess.Popen(
        cmd,
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
        cwd=str(MEETING_DIR),
    )
    toggl_id = toggl_start(name, DEFAULT_PROJECT)
    write_state({
        "pid": proc.pid,
        "name": name,
        "started": datetime.now().isoformat(timespec="seconds"),
        "log": log_path,
        "toggl_id": toggl_id,
        "project": DEFAULT_PROJECT,
        "calendar_minutes": minutes,
        "calendar_end": meeting["end"],
        "source": "daemon",
    })
    log(f"▶ Recording: {name!r} (pid {proc.pid}, {minutes}min, toggl {toggl_id})")


def append_pending(state: dict) -> None:
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "name": state.get("name"),
        "started": state.get("started"),
        "stopped": datetime.now().isoformat(timespec="seconds"),
        "project": state.get("project"),
        "calendar_minutes": state.get("calendar_minutes"),
        "log": state.get("log"),
    }
    with open(PENDING_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def stop_recording(state: dict, reason: str) -> None:
    pid = state.get("pid")
    name = state.get("name", "?")
    if not pid:
        return

    log(f"⏹ Stopping {name!r} ({reason})")
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        log(f"Process {pid} already dead")

    # Wait for transcription
    for _ in range(150):  # 5 minutes max
        try:
            os.kill(pid, 0)
            time.sleep(2)
        except OSError:
            break

    toggl_stop()
    audio_switch("default")
    append_pending(state)
    clear_state()
    log(f"✓ Done: {name}. Pending file logged.")

    # Quick non-blocking notification
    try:
        subprocess.run([
            "osascript", "-e",
            f'display notification "Filed {name}. Run /d357 file to extract notes." '
            f'with title "Meeting Daemon"'
        ], capture_output=True, timeout=3)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
def calendar_event_ended(state: dict) -> bool:
    end_str = state.get("calendar_end")
    if not end_str:
        return False
    try:
        end = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
        return datetime.now() > end
    except Exception:
        return False


def main() -> None:
    log(f"Meeting daemon started. Polling every {POLL_INTERVAL}s.")

    # Verify Outlook running
    result = subprocess.run(["pgrep", "-x", "Microsoft Outlook"], capture_output=True)
    if result.returncode != 0:
        log("Outlook not running — launching...")
        subprocess.Popen(["open", "-a", "Microsoft Outlook"])
        time.sleep(15)

    while True:
        try:
            state = read_state()
            recording = is_recording(state)
            meeting = get_current_meeting()

            if recording:
                # Stop conditions: calendar ended, or call ended >2min, or different meeting
                if calendar_event_ended(state):
                    stop_recording(state, "calendar event ended")
                elif state.get("source") == "daemon" and not is_call_active():
                    # Daemon-started + no call active for one full poll cycle = end
                    log("Call client closed — stopping daemon recording")
                    stop_recording(state, "call client closed")
                elif meeting and meeting["subject"] != state.get("name"):
                    log(f"Meeting changed: {state.get('name')} → {meeting['subject']}")
                    stop_recording(state, "meeting changed")
                # else: still going, do nothing
            elif meeting:
                ok, reason = should_record(meeting)
                if not ok:
                    log(f"Skip {meeting['subject']!r}: {reason}")
                elif not is_call_active():
                    log(f"Skip {meeting['subject']!r}: no call client active")
                elif not confirm_with_cancel(meeting["subject"]):
                    log(f"User canceled: {meeting['subject']!r}")
                else:
                    start_recording(meeting)

        except Exception as e:
            log(f"Loop error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Daemon stopped by user.")
        s = read_state()
        if is_recording(s):
            stop_recording(s, "daemon shutdown")
        sys.exit(0)
