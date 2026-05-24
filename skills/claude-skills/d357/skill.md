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

Background PID + metadata stored at `~/.claude/skills/d357/state.json`:
```json
{"pid": 12345, "name": "joe 1:1", "started": "2026-04-20T10:00:00", "log": "/tmp/d357-12345.log", "toggl_id": 98765, "project": "m5x2", "calendar_minutes": 30, "mic_only": false}
```

Absent or `pid: null` → no recording active.

## Commands

### `/d357 <name>` — start recording

1. Check state.json; if a recording is running, **fast-handoff** the previous recording:
   a. Save the old state (pid, name, log path, toggl_id, project, calendar_minutes, mic_only, started) to a local variable.
   b. Stop the old Toggl timer: `python3 ~/i446-monorepo/mcp/toggl_server/toggl_cli.py stop`.
   c. SIGTERM the old meet.py: `kill -TERM <old_pid>`. Do NOT wait for it to exit yet (it will transcribe in background).
   d. Log the handoff: `Stopping previous recording "<old_name>" → starting <new_name>`.
   e. After the new recording is started (step 8 below), **file the old recording in background**: poll `ps -p <old_pid>` every 2s until it exits, then tail the old log for `TXT ->`, compute duration, log points to 0分, read transcript, check new-notes, extract and file to vault/d357 (same as the normal stop flow steps 6-11). Write a temporary `/tmp/d357-handoff-<old_pid>.json` with the old state so the filing step has everything it needs, and clean it up after filing.
2. **Parse the input.** Split on comma: `<name>[, <start_time>]`. If a trailing HHMM or HH:MM is present after a comma, use it as the Toggl start time (backdated). Also parse `--no-teams` flag from the name for mic-only mode.
   - Examples: `/d357 Francois 1:1, 1000` → name="Francois 1:1", start_time=10:00
   - `/d357 SLT metrics` → name="SLT metrics", start_time=now (default)
3. **Kill stale dialogs + auto-switch audio** (Teams mode only, skip for `--no-teams`): Run `killall osascript 2>/dev/null` to dismiss any lingering warning dialogs from previous recordings. Then run `SwitchAudioSource -s "Meet Output"` to ensure system audio routes through BlackHole. If SwitchAudioSource is not installed or the device doesn't exist, warn but continue.
4. **Check Google Calendar** for a current event (now ± 5 min) using `mcp__google-calendar-mcp__list-events`. **Query both calendars in one call** by passing `calendarId: ["primary", "9nclf1b3vjqohorjefro3lfchk@group.calendar.google.com"]` (the second is the "Work" calendar — Microsoft events). If a match exists, capture:
   - `calendar_minutes`: the event's scheduled duration
   - `project`: `i9` if the event came from the Work calendar id; `m5x2` otherwise (default)
   - Prefer the calendar event title as the Toggl description if it differs from user input
   - Microsoft/Outlook events that aren't synced into the personal Google Work calendar won't be found — that's a known gap
5. **Start Toggl timer**: If `start_time` was provided, use `--at HH:MM` to backdate: `python3 ~/i446-monorepo/mcp/toggl_server/toggl_cli.py start "<name>" <project> --at <HH:MM>`. Otherwise start at now: `python3 ~/i446-monorepo/mcp/toggl_server/toggl_cli.py start "<name>" <project>`. Record the returned entry ID.
6. Launch recording in background using a **fixed log path** (`/tmp/d357-active.log`), not `$$`:
   ```bash
   cd ~/i446-monorepo/tools/meet && \
   nohup python3 meet.py "<name>" --domain d357 [--no-teams] [--max-duration <calendar_minutes>] > /tmp/d357-active.log 2>&1 &
   echo $!
   ```
7. Write state.json with PID, name, timestamp, log path (`/tmp/d357-active.log`), toggl_id, project, calendar_minutes (null if no calendar match), and `mic_only` (true if `--no-teams` was passed, else false).
8. Confirm in one line: `Recording → <name> (pid <pid>). Audio: both sides (<device>) | mic only. /d357 stop when done.` — say "both sides" if using Meet Output (BlackHole/Teams mode), "mic only" if `--no-teams` or no virtual audio device.

### `/d357 stop [HHMM]` — finalize

1. Read state.json. If no active PID, report `No recording active.` and exit.
2. **Parse optional end time.** If `HHMM` or `HH:MM` is provided after `stop`, use it as the Toggl end time. Compute duration as `end_time - start_time` (from state.json `started`). Otherwise stop at now and use actual Toggl duration.
3. **Stop Toggl timer**: If end time was provided, stop the timer and update the entry's stop time to `HHMM` via `toggl_cli.py stop` then update. Otherwise just `python3 ~/i446-monorepo/mcp/toggl_server/toggl_cli.py stop`.
4. Send SIGTERM: `kill -TERM <pid>`. meet.py stops recording, transcribes with Whisper, and saves a `.txt` transcript alongside the `.wav`.
5. Poll `ps -p <pid>` every 2s until the process exits (~0.1x realtime for transcription).
6. Tail the log for the `TXT ->` line to get the transcript path.
7. **Log points to 0分**: Use the computed duration (from end_time - start_time if provided, else calendar_minutes, else actual Toggl duration). Write that many 分 to the appropriate 0分 column based on `project` (m5x2->S, i9->R, etc.) via the neon excel lib.
7. Clear state.json (set `pid: null`).
8. **Read the transcript** from the `.txt` file.
9. **Check new-notes** (`~/vault/z_ibx/new-notes.md`) for any hand-written notes matching the meeting name.
10. **Extract and file** -- Claude Code generates the structured meeting note (summary, key points, decisions, action items, my notes) and writes it to `vault/d357/<M.W>/YYYY.MM.DD-<slug>.md`, where `<M.W>` is the Sunday-anchored week folder (same convention as 1n+: `sunday = date - timedelta(days=(date.weekday()+1)%7); folder = f"{sunday.month}.{(sunday.day-1)//7+1}"`). Create the week folder if it doesn't exist. If `mic_only` is true in state.json, prefix the frontmatter `title:` and the H1 heading with `1S ` (one-sided audio marker). Clear the meeting's section from new-notes.
11. **Link raw transcript** -- In the `## Raw Transcript` section of the d357 markdown file, include an Obsidian-style link to the `.txt` transcript file rather than inlining the full text. Format:
    ```markdown
    ## Raw Transcript

    *(N words; see [transcript](../../h335/i9/recordings/YYYY.MM.DD-HHMM-slug.txt))*
    ```
    The word count and relative path should match the actual transcript file written by meet.py.
11. Report: `Stopped. Filed -> <path>. Logged N 分 to <project>.`

### `/d357` (no args) — start recording with auto-detected name

If a recording is running, report status: `Recording: <name> since <HH:MM> (pid <pid>)`.

If no recording is running, **auto-detect the meeting name** and start recording:
1. Query Google Calendar for the current event (happening now ± 5 min) using `mcp__google-calendar-mcp__list-events` with `calendarId: ["primary", "9nclf1b3vjqohorjefro3lfchk@group.calendar.google.com"]` (primary + Work calendars).
2. If found, use the event title as the meeting name. Set `project = i9` if it's a Work-calendar event, else `m5x2`.
3. If no current event, fall back to `"meeting YYYY.MM.DD HHmm"`.
4. Proceed with the standard start flow (launch meet.py, write state.json, confirm).

### `/d357 status` — show current state

Report `Recording: <name> since <HH:MM> (pid <pid>)` if active, else `No recording active.`

## Notes

- **Excel/OneDrive not required** — meet.py writes markdown to the vault directly.
- **Whisper model:** `base.en` (default, ~150MB download on first run). Override with `--model small.en` for better accuracy at 3× the time.
- **Teams mode requires one-time setup** (BlackHole virtual audio device); see the docstring at the top of meet.py.
- The `d357` domain maps to `vault/d357/<M.W>/` (Sunday-anchored week folders, matching 1n+).
- **Sweeper safety net:** `~/i446-monorepo/tools/meet/d357-organize.py` runs hourly via cron and moves any loose `YYYY.MM.DD-*.md` at the `d357/` root into the right week folder. The sweeper does NOT add the `1S ` prefix — that decision lives in the skill's stop flow where `mic_only` is known.
- **Auto-stop (calendar):** When `calendar_minutes` is available, pass `--max-duration <minutes>` so meet.py auto-stops when the event should end. The process still runs transcription and saves normally.
- **Auto-stop (idle):** meet.py auto-stops after 10 minutes of silence once conversation has been detected (default, override with `--idle-timeout <min>`). Both auto-stops send a macOS notification.
- **Watchdog:** `~/i446-monorepo/scripts/d357-watchdog.py` runs every 10 min via `com.jm.d357-watchdog`. Reads state.json and notifies if (1) the recording pid has died (meet.py crashed) or (2) elapsed >= 2× calendar duration (or >=90 min if no calendar). Rate-limited to one overrun nudge every 30 min. Logs at `/tmp/d357-watchdog.log`.

## Regression tests

| Input | Expected |
|-------|----------|
| `/d357 joe 1:1` | Launches meet.py in bg, writes state.json with pid, confirms |
| `/d357 joe 1:1` (while one is running) | Fast-handoff: stops old Toggl, SIGTERMs old meet.py, starts new recording, files old transcript in background |
| `/d357 stop` | SIGTERM, waits for filing, reports path, clears state |
| `/d357 stop` (nothing running) | Reports "No recording active." |
| `/d357` (nothing running) | Auto-detects calendar event name, starts recording |
| `/d357` (while running) | Reports current recording status |
| `/d357 standup --no-teams` | Launches mic-only (in-person) |
