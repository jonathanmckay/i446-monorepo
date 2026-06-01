---
name: "d357"
description: "Record a meeting as a transcript. Launches meet.py in the background; stop with /d357 stop to finalize transcription and file notes to vault/d357/. Usage: /d357 <meeting name> | /d357 stop | /d357 status"
user-invocable: true
---

# Record Meeting (/d357)

Wraps `~/i446-monorepo/tools/meet/meet.py`:
- Records mic + system audio by default (add `--no-teams` for mic-only)
- Transcribes locally with faster-whisper (base.en)
- Extracts notes + action items via Claude
- Files to `vault/d357/YYYY.MM.DD-<slug>.md`

## State

Background metadata stored at `~/.claude/skills/d357/state.json`:
```json
{"pid": 12345, "tmux": "d357", "name": "joe 1:1", "started": "2026-04-20T10:00:00", "log": "/tmp/d357-active.log", "toggl_id": 98765, "project": "m5x2", "calendar_minutes": 30, "mic_only": false}
```

Absent or `pid: null` → no recording active. The `tmux` field tracks the tmux session name.

## Commands

### `/d357 <name>` — start recording

1. Check state.json; if a recording is running, **auto-stop it** before starting the new one:
   a. Save `prev_mic_only = state.mic_only` (carry forward audio config).
   b. Stop Toggl timer for the previous recording.
   c. Send one `tmux send-keys -t d357 C-c` to stop meet.py.
   d. Poll for transcript completion (same as `/d357 stop` step 5): wait for `TXT →` or `Done!` in the log, up to 120s (shorter than normal stop; we want to get the new recording going).
   e. Read the transcript path from the log.
   f. Clear state.json (`pid: null`).
   g. **File the old meeting in the background** using an Agent: pass it the old meeting's name, transcript path, project, mic_only, and Toggl duration. The agent runs the full filing flow (steps 9-12 from `/d357 stop`) while the new recording starts. Do NOT block on this.
   h. Report: `⏹ Stopped: <old name>. Filing in background.`
2. **Parse the input.** Split on comma: `<name>[, <start_time>]`. If a trailing HHMM or HH:MM is present after a comma, use it as the Toggl start time (backdated). Also parse `--no-teams` flag from the name for mic-only mode.
   - Examples: `/d357 Francois 1:1, 1000` → name="Francois 1:1", start_time=10:00
   - `/d357 SLT metrics` → name="SLT metrics", start_time=now (default)
3. **Audio pre-flight check** (skip for `--no-teams`; also skip if `prev_mic_only` is true, and auto-set mic-only instead):
   a. Kill stale osascript dialogs: `killall osascript 2>/dev/null`
   b. **Detect AirPods HFP mode** — if AirPods are connected but in HFP mode (1ch output, 24kHz sample rate), the Meet Output multi-output device will silently fail to route to BlackHole. Check with:
      ```python
      python3 -c "import sounddevice as sd; [print(f'{d[\"name\"]} out={d[\"max_output_channels\"]} rate={d[\"default_samplerate\"]}') for d in sd.query_devices() if 'AirPods' in d['name'] and d['max_output_channels'] > 0]"
      ```
      If output channels == 1 and rate == 24000: AirPods are in HFP mode. **Auto-switch to mic-only** and warn: `⚠ AirPods in HFP mode (Teams grabbed mic) — recording mic-only.`
   c. If not HFP, switch system output: `SwitchAudioSource -s "Meet Output"`
   d. If AirPods are BT-connected but missing from `SwitchAudioSource -a -t output`, reconnect: `blueutil --disconnect <MAC> && sleep 2 && blueutil --connect <MAC>` (MAC: `70-F9-4A-87-EC-D7`)
4. **Check Google Calendar** for a current event (now ± 5 min) using `mcp__google-calendar-mcp__list-events`. **Query both calendars in one call** by passing `calendarId: ["primary", "9nclf1b3vjqohorjefro3lfchk@group.calendar.google.com"]` (the second is the "Work" calendar — Microsoft events). If a match exists, capture:
   - `calendar_minutes`: the event's scheduled duration
   - `project`: `i9` if the event came from the Work calendar id; `m5x2` otherwise (default)
   - Prefer the calendar event title as the Toggl description if it differs from user input
5. **Start Toggl timer**: If `start_time` was provided, use `--at HH:MM` to backdate. Otherwise start at now. Record the returned entry ID.
6. **Launch recording in tmux** (NOT nohup — nohup dies from SIGTERM when Claude's bash subprocess exits):
   ```bash
   tmux new-session -d -s d357 "cd ~/i446-monorepo/tools/meet && PYTHONUNBUFFERED=1 python3 -u meet.py '<name>' --domain d357 [--no-teams] [--mic '<mic_name>'] [--max-duration <calendar_minutes>] [--idle-timeout 0] > /tmp/d357-active.log 2>&1"
   ```
   **CRITICAL**: Use `>` redirect, NOT `| tee`. Tee creates a pipe; when tmux sends Ctrl-C, the pipe breaks and the shell exits before meet.py can finish transcription.

   **Idle timeout rules**:
   - If mic-only mode (`--no-teams`): always pass `--idle-timeout 0` (disable). The mic signal is too intermittent for idle detection to work reliably.
   - If teams mode with BlackHole: use default idle timeout (5 min).

7. **Post-launch health check** (THE CRITICAL STEP — do not skip):
   Wait 15 seconds, then verify recording is healthy:
   ```bash
   sleep 15
   tmux has-session -t d357 2>/dev/null && echo "session alive" || echo "SESSION DEAD"
   tail -5 /tmp/d357-active.log
   ```
   Check the log for:
   - `Recording... press Ctrl+C to stop` → good, recording is running
   - `Done!` or `Stopped` → **BAD**: recording already exited. Diagnose and restart immediately.
   - `⚠  Call audio device has zero signal` → expected if in HFP mode; should have been caught in pre-flight
   - No output at all → process crashed, check stderr in log

   **If the session died or the recording exited early**: diagnose from the log, fix the issue (usually: switch to mic-only, or reconnect AirPods), and restart. Do NOT report success to the user if the recording is dead. The user cannot babysit this.

8. Write state.json with tmux session name, PID (from `tmux list-panes -t d357 -F '#{pane_pid}'`), name, timestamp, log path, toggl_id, project, calendar_minutes, and mic_only.
8b. **Emit prof arrival event** (for professionalism daemon scoring):
    ```bash
    python3 ~/i446-monorepo/tools/prof/log_arrival.py start \
        --name "<name>" \
        ${calendar_minutes:+--calendar-minutes $calendar_minutes} \
        ${scheduled_start:+--scheduled-start "$scheduled_start"}
    ```
    `scheduled_start` is the ISO8601 start from the calendar event found in step 4 (with offset). Omit both flags if no calendar match.
9. Confirm in one line: `Recording → <name> (tmux:d357). Audio: <mode>. /d357 stop when done.`

### `/d357 stop [HHMM]` — finalize

1. Read state.json. If no active recording, report `No recording active.` and exit.
2. **Parse optional end time.** If `HHMM` or `HH:MM` is provided after `stop`, use it as the Toggl end time.
3. **Stop Toggl timer**.
4. **Stop recording via tmux**: Send SIGINT (not SIGTERM) to the tmux session so meet.py handles it gracefully:
   ```bash
   tmux send-keys -t d357 C-c
   ```
   Send this **once**. Do not spam `C-c`, `kill -INT`, or `kill -TERM`; repeated interrupts can land while Whisper is saving artifacts. `meet.py` now protects WAV/TXT writes, but the correct operator behavior is one stop request, then wait.
5. **Wait for transcription** — poll the log for `TXT →` or `Done!` every 2s, up to 300s. meet.py needs time to save the wav and run Whisper.
   ```bash
   for i in $(seq 1 150); do
       sleep 2
       if grep -q "Done!\|TXT →" /tmp/d357-active.log 2>/dev/null; then break; fi
       if ! tmux has-session -t d357 2>/dev/null; then break; fi
   done
   ```
6. **Extract transcript path** from the log (`TXT →` line). If no transcript was written, check for the wav file and run Whisper manually.
7. **Log points to 0分**: Use the computed duration. Write to the appropriate column (i9→R, m5x2→S, etc.) via ix-osa.sh. **CRITICAL: use `formula` not `value`** to preserve existing formula chains. Pattern:
    ```applescript
    set theCell to range (targetCol & todayRow) of theSheet
    set oldFormula to formula of theCell
    if oldFormula = "" or oldFormula = "0" then
        set formula of theCell to "=0+" & N
    else if character 1 of oldFormula is not "=" then
        set formula of theCell to "=" & oldFormula & "+" & N
    else
        set formula of theCell to oldFormula & "+" & N
    end if
    ```
    Never use `set value of range ... to oldVal & "+N"` as this destroys existing formulas.
8. Clear state.json (set `pid: null`).
8b. **Emit prof stop event** (for professionalism daemon scoring):
    ```bash
    python3 ~/i446-monorepo/tools/prof/log_arrival.py stop --name "<name>"
    ```
9. **Read the transcript** from the `.txt` file.
10. **Check new-notes** (`~/vault/z_ibx/new-notes.md`) for hand-written notes matching the meeting name.
11. **Extract and file** — generate the structured meeting note and write to `vault/d357/<M.W>/YYYY.MM.DD-<slug>.md`. If `mic_only` is true, prefix title and H1 with `1S `.
12. **Link raw transcript**:
    ```markdown
    ## Raw Transcript

    *(N words; see [transcript](../../h335/i9/recordings/YYYY.MM.DD-HHMM-slug.txt))*
    ```
13. Report: `Stopped. Filed -> <path>. Logged N 分 to <project>.`

### `/d357` (no args) — start recording with auto-detected name

If a recording is running, report status: `Recording: <name> since <HH:MM> (tmux:d357)`.

If no recording is running, auto-detect from Google Calendar (both primary + Work calendars, ±5 min). Fall back to `"meeting YYYY.MM.DD HHmm"`. Proceed with standard start flow.

### `/d357 status` — show current state

Report `Recording: <name> since <HH:MM> (tmux:d357)` if active, else `No recording active.`

## Audio Routing Reference

### How it's supposed to work (A2DP mode)
```
Teams → System Output ("Meet Output") → [AirPods (you hear) + BlackHole (capture)]
MacBook Mic → meet.py mic stream
BlackHole → meet.py system stream
Both streams mixed → wav → Whisper → transcript
```

### What breaks it: AirPods HFP mode
When Teams uses the AirPods mic for the call, macOS forces AirPods into HFP mode (mono, 24kHz). The Meet Output multi-output device was configured for A2DP (stereo, 48kHz). Channel mismatch breaks routing to BlackHole silently.

**Detection**: AirPods output device shows `max_output_channels=1, default_samplerate=24000`
**Mitigation**: Auto-switch to mic-only mode. The MacBook mic picks up your voice; the remote side is partially audible if AirPods have any bleed.
**Prevention**: In Teams Settings > Devices, set mic to "MacBook Pro Microphone" (not AirPods). This keeps AirPods in A2DP mode.

### Fallback chain
1. Teams mode (BlackHole + mic) — best quality, captures both sides
2. Mic-only with MacBook mic — captures your side clearly, remote side faintly via speaker/AirPods bleed
3. If no audio devices work — abort and tell the user

## Notes

- **tmux, not nohup**: Always launch in tmux. `nohup &` dies from SIGTERM when Claude's bash subprocess exits.
- **No tee**: Always `> /tmp/d357-active.log 2>&1`, never `| tee`. Tee creates a pipe that breaks on Ctrl-C.
- **One stop signal**: Stop with a single `tmux send-keys ... C-c`, then wait. If the log shows WAV saved but no TXT, recover from the WAV; do not escalate until artifact salvage is complete.
- **Whisper model:** `base.en` (default, ~150MB download on first run).
- The `d357` domain maps to `vault/d357/<M.W>/` (Sunday-anchored week folders).
- **Sweeper safety net:** `d357-organize.py` runs hourly via cron.
- **Auto-stop (calendar):** When `calendar_minutes` is available, pass `--max-duration <minutes>`.
- **Idle timeout**: Disabled for mic-only mode (`--idle-timeout 0`). Default 5 min for teams mode.
- **Watchdog:** `d357-watchdog.py` runs every 10 min via launchd.
- **`--mic` flag**: Override mic device. Use `--mic AirPods` to record from AirPods mic instead of MacBook mic.
- **blueutil**: Installed at `/opt/homebrew/bin/blueutil`. Use to reconnect AirPods when they're BT-connected but missing from CoreAudio.

## Regression tests

| Input | Expected |
|-------|----------|
| `/d357 joe 1:1` | tmux session, state.json, health check passes, confirms |
| `/d357 joe 1:1` (while one is running) | Auto-stops current, files in background, starts new recording |
| `/d357 joe 1:1` (prev was mic_only) | Inherits mic-only mode, skips audio pre-flight |
| `/d357 stop` | Ctrl-C via tmux, waits for transcript, files, reports |
| `/d357 stop` (nothing running) | Reports "No recording active." |
| `/d357` (nothing running) | Auto-detects calendar event, starts recording |
| `/d357` (while running) | Reports current recording status |
| `/d357 standup --no-teams` | mic-only, idle-timeout 0 |
| AirPods in HFP mode | Auto-detects, switches to mic-only, warns |
| tmux session dies post-launch | Health check catches it, diagnoses, restarts |
