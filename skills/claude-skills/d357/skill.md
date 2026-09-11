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

Background metadata stored at `~/.local/state/jm/d357-state.json` (machine-local;
NOT synced across machines, so a PID written on one box never leaks to another —
see `vault/z_meta/architecture.md` hazard #1):
```json
{"pid": 12345, "tmux": "d357", "name": "joe 1:1", "started": "2026-04-20T10:00:00", "log": "/tmp/d357-active.log", "toggl_id": 98765, "project": "m5x2", "calendar_minutes": 30, "mic_only": false}
```

Absent or `pid: null` → no recording active. The `tmux` field tracks the tmux session name.

**Liveness guard (check BEFORE trusting any active recording).** A state file
claiming a recording is active is only valid if the process is actually alive and
recent. Before acting on a non-null `pid`, verify all three:
1. `kill -0 <pid>` succeeds (process exists), AND
2. `tmux has-session -t <tmux>` succeeds, AND
3. `started` is today (parse the date; a recording from a prior day is stale).

If any check fails, the state is **stale** (crashed, killed, or a leftover from a
prior session): clear it (`pid: null`), do NOT try to stop/transcribe it, and
proceed as if no recording is active. This is what prevents the 43-hour zombie
(2026-06-10): a stale pid was trusted and meet.py was left recording silence for
two days.

## Commands

### `/d357 <name>` — start recording

1. Check state.json; if a recording is running, **auto-stop it** before starting the new one:
   a. Save `prev_mic_only = state.mic_only` (carry forward audio config).
   b. Stop Toggl timer for the previous recording.
   c. Send one `tmux send-keys -t d357 C-c` to stop meet.py.
   d. Poll for transcript completion (same as `/d357 stop` step 4): wait for `TXT →` or `Done!` in the log, up to 120s (shorter than normal stop; we want to get the new recording going).
   e. Read the transcript path from the log.
   f. Clear state.json (`pid: null`).
   g. **File the old meeting in the background** using an Agent: pass it the old meeting's name, transcript path, project, mic_only, and Toggl duration. The agent runs the full filing flow (steps 8-11 from `/d357 stop`) while the new recording starts. Do NOT block on this.
   h. Report: `⏹ Stopped: <old name>. Filing in background.`
2. **Parse the input.** Split on comma: `<name>[, <start_time>]`. If a trailing HHMM or HH:MM is present after a comma, use it as the Toggl start time (backdated). Also parse `--no-teams` flag from the name for mic-only mode.
   - Examples: `/d357 Francois 1:1, 1000` → name="Francois 1:1", start_time=10:00
   - `/d357 SLT metrics` → name="SLT metrics", start_time=now (default)
3. **Check Google Calendar** for a current event (now ± 5 min) using `mcp__google-calendar-mcp__list-events`. **Query both calendars in one call** by passing `calendarId: ["primary", "l20n3a79v2lq68fod4de3lvp1ba2iqft@import.calendar.google.com"]` (the second is "MSFT (Slow Sync)" — the ICS import of the Microsoft/Outlook calendar; the old `9nclf1b3...@group.calendar.google.com` Work calendar no longer exists). Match by time overlap with now, ignoring all-day/`transparent` events (OOF banners). If a match exists, capture:
   - `calendar_minutes`: the event's scheduled duration
   - `project`: `i9` if the event came from the MSFT calendar id; `m5x2` otherwise (default)
   - Prefer the calendar event title as the Toggl description if it differs from user input

   **Fallback — janus caches.** The MSFT import calendar is slow-sync and can lag or miss events. If the MCP query returns no current event, check the janus caches (written by `~/i446-monorepo/tools/tg/{outlook_client,gcal_client}.py`):
   - `~/.cache/janus/outlook-YYYY-MM-DD.json` — Outlook via Agency MCP; events have `subject`, `start`, `end` (naive local-time strings with `start_tz`/`end_tz` Windows tz names)
   - `~/.cache/janus/gcal-YYYY-MM-DD.json` — all calendars visible to the m5c7 account; events from `"calendar": "Calendar"` are the MSFT mirror (treat as `i9`); timestamps may be UTC (`Z` suffix)

   An event from either cache that overlaps now counts as a calendar match (same captures as above; Outlook-sourced events → `i9`). The caches only refresh while janus is running (5-min TTL), so check the file mtime — if older than ~30 min, treat its absence of a match as inconclusive rather than authoritative. If no source matches but the meeting name implies Microsoft work (Xbox, GitHub, CoreAI, SLT, names of MSFT coworkers), default the project to `i9` instead of `m5x2`.
4. **Start Toggl timer FIRST** — before the audio pre-flight. Calling the Toggl CLI is the first *write* of the start flow so the time entry is pinned at invocation and the slow, failure-prone audio device juggling (step 5) can't delay or skew it. (Project, description, and `calendar_minutes` are resolved by the calendar check in step 3; the CLI has no `edit` subcommand, so the project must be known before the entry is created.) If `start_time` was provided, use `--at HH:MM` to backdate. Otherwise start at now. Record the returned entry ID.
5. **Audio pre-flight check** (skip for `--no-teams`; also skip if `prev_mic_only` is true, and auto-set mic-only instead):
   a. Kill stale osascript dialogs: `killall osascript 2>/dev/null`
   b. **Detect AirPods HFP mode** — if AirPods are connected but in HFP mode (1ch output, 24kHz sample rate), the Meet Output multi-output device will silently fail to route to BlackHole. Check with:
      ```python
      python3 -c "import sounddevice as sd; [print(f'{d[\"name\"]} out={d[\"max_output_channels\"]} rate={d[\"default_samplerate\"]}') for d in sd.query_devices() if 'AirPods' in d['name'] and d['max_output_channels'] > 0]"
      ```
      If output channels == 1 and rate == 24000: AirPods are in HFP mode. **Auto-switch to mic-only** and warn LOUDLY (regression 2026-06-11: two meetings lost the remote side and the user missed the one-line warning):
      - Set the terminal tab orange: `~/i446-monorepo/scripts/term-color.sh orange` (non-fatal degradation)
      - Send a macOS notification so it's visible over a full-screen Teams window: `osascript -e 'display notification "Remote side will NOT be captured (AirPods in HFP). Fix: Teams Settings > Devices > Mic = MacBook Pro Microphone" with title "d357: mic-only recording"'`
      - Make `⚠ MIC-ONLY — remote side will NOT be captured` the FIRST line of the confirmation message, not a trailing note
      - **Launch teamstap** (see Process Tap section) so the remote side is captured anyway: the mic-only fallback then loses nothing but channel separation.
   c. If not HFP, switch system output: `SwitchAudioSource -s "Meet Output"`
   d. If AirPods are BT-connected but missing from `SwitchAudioSource -a -t output`, reconnect: `blueutil --disconnect <MAC> && sleep 2 && blueutil --connect <MAC>` (MAC: `70-F9-4A-87-EC-D7`)
      **MID-CALL GUARD**: NEVER bounce Bluetooth if a call is likely in progress — it cuts the user's live call audio for several seconds (regression: 2026-06-04 Adam Habig call, user lost audio mid-conversation). Treat a call as likely in progress when the current calendar event window (step 3) covers now, or when the meeting name was given for a meeting that has already started. In that case skip the bounce entirely and fall back to mic-only. The bounce is also futile mid-call: the conferencing app re-grabs the AirPods mic immediately and forces HFP again.
6. **Launch recording in tmux** (NOT nohup — nohup dies from SIGTERM when Claude's bash subprocess exits):
   ```bash
   tmux new-session -d -s d357 "cd ~/i446-monorepo/tools/meet && PYTHONUNBUFFERED=1 python3 -u meet.py '<name>' --domain d357 [--no-teams] [--mic '<mic_name>'] [--max-duration <calendar_minutes>] [--idle-timeout 0] > /tmp/d357-active.log 2>&1"
   ```
   **CRITICAL**: Use `>` redirect, NOT `| tee`. Tee creates a pipe; when tmux sends Ctrl-C, the pipe breaks and the shell exits before meet.py can finish transcription.

   **Idle timeout rules**:
   - If mic-only mode (`--no-teams`): always pass `--idle-timeout 0` (disable). The mic signal is too intermittent for idle detection to work reliably.
   - If teams mode with BlackHole: use default idle timeout (5 min).

7. **Post-launch health check** (THE CRITICAL STEP — do not skip):

   There are TWO things to verify, and they are separate: **liveness** (the process
   is recording) and **audio health** (the call audio is actually being captured).
   The audio verdict is what caused the 2026-06-21 inconsistency: the old check
   reported success on the `Recording...` banner at 15s, but meet.py's audio verdict
   is time-delayed (it lands at ~15s and 60s), so a green report fired while the
   remote side was silently dead. **Do not declare audio health from the `Recording...`
   banner.** meet.py is the single source of truth: it now emits one machine-readable
   line, `AUDIO_VERDICT <state> ...`, and the report must relay THAT, not a guess.

   **7a. Liveness (immediate).**
   ```bash
   sleep 15
   tmux has-session -t d357 2>/dev/null && echo "session alive" || echo "SESSION DEAD"
   tail -5 /tmp/d357-active.log
   ```
   - `Recording... press Ctrl+C to stop` → process is alive (liveness only, NOT audio).
   - `Done!` or `Stopped` → **BAD**: recording already exited. Diagnose and restart.
   - No output at all → process crashed, check stderr in log.

   **7b. Audio verdict (wait for it — do not skip to the report).** Poll the log for
   the `AUDIO_VERDICT` line meet.py emits (mic-only → at startup; teams mode → at the
   15s early check). Wait up to ~25s for a teams-mode verdict:
   ```bash
   for i in $(seq 1 13); do
       v=$(grep -m1 '^AUDIO_VERDICT' /tmp/d357-active.log 2>/dev/null) && break
       tmux has-session -t d357 2>/dev/null || break
       sleep 2
   done
   echo "verdict: ${v:-<none yet>}"
   ```
   Interpret and **report exactly what the verdict says** (one judge, no parallel heuristic):
   - `AUDIO_VERDICT ok channels=both` → both sides captured. Report "Audio: teams mode (both sides)".
   - `AUDIO_VERDICT mic-only reason=no-teams` → expected for `--no-teams`. Report "Audio: mic-only".
   - `AUDIO_VERDICT degraded reason=call-zero-signal` → **the remote side is NOT being
     captured.** Do NOT report success. Cross-check by probing BlackHole directly
     (genuine silence carries a ~0.0003 noise floor; sustained exact `0.000000` = not captured):
     ```bash
     python3 -c "import sounddevice as sd,numpy as np; i=next(k for k,d in enumerate(sd.query_devices()) if 'BlackHole' in d['name'] and d['max_input_channels']>0); r=sd.rec(int(3*48000),samplerate=48000,channels=2,device=i); sd.wait(); print('BlackHole rms=%.6f'%float(np.sqrt((r**2).mean())))"
     ```
     If zero, remediate before handing back: for a **Teams-app** meeting launch teamstap
     (see Process Tap section); if teamstap also reads exact `0.000000`, the call audio
     is on a device neither path taps — surface the one-line fix to the user
     (**Teams → Settings → Devices → Speaker → "Meet Output"**, which feeds the
     already-running BlackHole stream) and report the remote side as **not yet captured**.
   - No verdict after ~25s but session alive → report audio as **unconfirmed**, not green.

   **The rule: the success line you give the user must match meet.py's `AUDIO_VERDICT`.**
   Never report "both sides" on a `degraded` verdict or a zero BlackHole probe.

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
2. **Stop the Toggl timer FIRST.** This is the first action after the active-recording guard — ahead of stopping the recording and the transcription wait — so the logged duration is pinned to when you invoked stop, not when Whisper finishes. Parse an optional end time (`HHMM` or `HH:MM` after `stop`) and stop the Toggl timer with that end time if provided; otherwise stop at now.
3. **Stop recording via tmux**: Send SIGINT (not SIGTERM) to the tmux session so meet.py handles it gracefully:
   ```bash
   tmux send-keys -t d357 C-c
   ```
   Send this **once**. Do not spam `C-c`, `kill -INT`, or `kill -TERM`; repeated interrupts can land while Whisper is saving artifacts. `meet.py` now protects WAV/TXT writes, but the correct operator behavior is one stop request, then wait.
4. **Wait for transcription** — poll the log for `TXT →` or `Done!` every 2s, up to 300s. meet.py needs time to save the wav and run Whisper.
   ```bash
   for i in $(seq 1 150); do
       sleep 2
       if grep -q "Done!\|TXT →" /tmp/d357-active.log 2>/dev/null; then break; fi
       if ! tmux has-session -t d357 2>/dev/null; then break; fi
   done
   ```
5. **Extract transcript path** from the log (`TXT →` line). If no transcript was written, check for the wav file and run Whisper manually.
6. **Log points to 0分**: Use the computed duration. Append to the appropriate column (i9→R, m5x2→S, etc.) via the excel-http daemon — NEVER raw AppleScript/ix-osa.sh (daemon writes are journaled in the neon audit ledger; raw writes trip the chain check). Always pass `src` naming the meeting:
    ```bash
    ssh ix "curl -s -X POST localhost:9876/append -H 'Content-Type: application/json' \
        -d '{"sheet":"0分","col":"R","date":"M/D","value":"+N","src":"d357 <meeting name>"}'"
    ```
    The daemon handles formula-append semantics (empty cell, bare number, existing formula) itself. If the response contains `"chain": "broken"`, report it — the cell was edited outside the daemon since its last journaled write.
7. Clear state.json (set `pid: null`).
7b. **Emit prof stop event** (for professionalism daemon scoring):
    ```bash
    python3 ~/i446-monorepo/tools/prof/log_arrival.py stop --name "<name>"
    ```
8. **Read the transcript** from the `.txt` file.
9. **Check new-notes** (`~/vault/z_ibx/new-notes.md`) for hand-written notes matching the meeting name.
10. **Extract and file** — generate the structured meeting note and write to `vault/d357/<M.W>/YYYY.MM.DD-<slug>.md`. If `mic_only` is true, prefix title and H1 with `1S `.
11. **Link raw transcript**:
    ```markdown
    ## Raw Transcript

    *(N words; see [transcript](../../h335/i9/recordings/YYYY.MM.DD-HHMM-slug.txt))*
    ```
12. Report: `Stopped. Filed -> <path>. Logged N 分 to <project>.`

### `/d357` (no args) — start recording with auto-detected name

If a recording is running, report status: `Recording: <name> since <HH:MM> (tmux:d357)`.

If no recording is running, auto-detect from Google Calendar (both primary + Work calendars, ±5 min). Fall back to `"meeting YYYY.MM.DD HHmm"`. Proceed with standard start flow.

### `/d357 status` — show current state

Report `Recording: <name> since <HH:MM> (tmux:d357)` if active, else `No recording active.`

## Process Tap (teamstap) — preferred call-audio capture

`~/i446-monorepo/tools/meet/teamstap/teamstap` (Swift, ScreenCaptureKit) captures
the audio OUTPUT of the Teams app directly from the process, regardless of which
output device Teams uses or what BT profile the AirPods are in. Verified live
2026-06-11: captured call audio while AirPods were in HFP, Teams pinned to its own
devices, and BlackHole read zero. This obsoletes the Meet Output/BlackHole path
for Teams meetings.

- Launch alongside meet.py (own tmux session) whenever recording a Teams meeting:
  ```bash
  tmux new-session -d -s teamstap "~/i446-monorepo/tools/meet/teamstap/teamstap \
      --out '<recordings>/YYYY.MM.DD-HHMM-<slug>-remote.wav' \
      --max-seconds <calendar_minutes*60+300> 2> /tmp/teamstap.log"
  ```
- It tails liveness stats to /tmp/teamstap.log (`stat: frames=... rms=...`).
- **Capture-health check (do this ~30s after launch, during a live call).** teamstap
  is routing-dependent: it sometimes attaches to the Teams process but reads pure
  digital silence (`rms=0.000000 peak=0.000000`) while the call audio is actually
  flowing through the system output device to BlackHole instead (observed live
  2026-06-15: teamstap zero, BlackHole rms 0.068). Genuine silence still carries a
  codec noise floor (~`rms=0.0003`), so **sustained exact `0.000000`** = teamstap is
  NOT capturing this call. When you see that, probe BlackHole:
  ```bash
  python3 -c "import sounddevice as sd,numpy as np; i=next(k for k,d in enumerate(sd.query_devices()) if 'BlackHole' in d['name'] and d['max_input_channels']>0); r=sd.rec(int(2*48000),samplerate=48000,channels=2,device=i); sd.wait(); print('BlackHole rms=%.6f'%float(np.sqrt((r**2).mean())))"
  ```
  If BlackHole has signal (rms ≫ 0), **fall back to the BlackHole path**: kill teamstap
  and the mic-only meet.py, and relaunch meet.py in *teams mode* (`mic + BlackHole`,
  no `--no-teams`) which captures both sides in one stream. Set `mic_only: false` in
  state and drop the `teamstap_wav` field. Only a few seconds of partial audio is lost.
- **Stop**: `tmux send-keys -t teamstap C-c` (finalizes the WAV), or it self-stops
  at --max-seconds. Check the log for `stopped.`.
- **At /d357 stop**: stop teamstap too, then transcribe the remote wav with the
  canonical helper — **do NOT hand-roll a faster-whisper call**:
  ```bash
  python3 ~/i446-monorepo/tools/meet/meet.py --transcribe '<remote.wav>'
  ```
  This writes `<remote>.txt` and prints the transcript. It passes the WAV *path* to
  faster-whisper, which decodes + resamples 48kHz→16kHz via PyAV. (The teamstap WAV
  is 48kHz mono; decoding it into a raw ndarray and passing THAT to `transcribe`
  silently yields an empty transcript — faster-whisper does not resample ndarrays,
  so 48kHz samples produce NaN mel features. The `--transcribe` path avoids that.)
  Exit code is non-zero on an empty transcript, so a silently-lost remote side
  surfaces instead of filing a one-sided note. Then merge both transcripts in the
  note — mic transcript = JM's side, remote wav = everyone else (label accordingly).
- TCC: needs "System Audio Recording" permission (granted to the terminal app;
  if capture yields zeros, check System Settings > Privacy > Screen & System
  Audio Recording).
- Rebuild after edits: `cd ~/i446-monorepo/tools/meet/teamstap && swiftc -O teamstap.swift -o teamstap`
- meet.py keeps recording the MacBook mic (`--no-teams --idle-timeout 0`) for JM's
  side; teamstap replaces the BlackHole/system-audio leg.

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
- **blueutil**: Installed at `/opt/homebrew/bin/blueutil`. Use to reconnect AirPods when they're BT-connected but missing from CoreAudio — but ONLY when no call is in progress (see pre-flight step 3d MID-CALL GUARD); bouncing BT mid-call cuts the user's live audio.

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
| AirPods missing from CoreAudio, call in progress | Skips BT bounce (no audio cut), records mic-only |
