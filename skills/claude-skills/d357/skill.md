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

1. Check state.json; if a recording is running, **abort** and tell the user to stop it first.
2. Parse flags from the name: `--no-teams` for mic-only mode (in-person). Default captures both mic + system audio.
3. **Check Google Calendar** for a current event (now +/- 5 min) using `mcp__google-calendar-mcp__list-events`. If a match exists, capture:
   - `calendar_minutes`: the event's scheduled duration
   - `project`: infer from calendar name/color (m5x2 calendar events -> m5x2, work calendar -> i9, default m5x2)
   - Prefer the calendar event title as the Toggl description if it differs from user input
4. **Start Toggl timer**: `python3 ~/i446-monorepo/mcp/toggl_server/toggl_cli.py start "<name>" <project>`. Record the returned entry ID.
5. Launch recording in background:
   ```bash
   cd ~/i446-monorepo/tools/meet && \
   nohup python3 meet.py "<name>" --domain d357 [--no-teams] [--max-duration <calendar_minutes>] > /tmp/d357-$$.log 2>&1 &
   echo $!
   ```
6. Write state.json with PID, name, timestamp, log path, toggl_id, project, calendar_minutes (null if no calendar match), and `mic_only` (true if `--no-teams` was passed, else false).
7. Confirm in one line: `Recording → <name> (pid <pid>). /d357 stop when done.`

### `/d357 stop` — finalize

1. Read state.json. If no active PID, report `No recording active.` and exit.
2. **Stop Toggl timer**: `python3 ~/i446-monorepo/mcp/toggl_server/toggl_cli.py stop`. Note the duration.
3. Send SIGTERM: `kill -TERM <pid>`. meet.py stops recording, transcribes with Whisper, and saves a `.txt` transcript alongside the `.wav`.
4. Poll `ps -p <pid>` every 2s until the process exits (~0.1x realtime for transcription).
5. Tail the log for the `TXT ->` line to get the transcript path.
6. **Log points to 0分**: Use `calendar_minutes` from state.json if available, else actual Toggl duration. Write that many 分 to the appropriate 0分 column based on `project` (m5x2->AB, i9->AA, etc.) via the "Append to 0分" AppleScript template through ix-osa.sh.
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
1. Query Google Calendar for the current event (happening now ± 5 min) using `mcp__google-calendar-mcp__list-events`.
2. If found, use the event title as the meeting name.
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

## Regression tests

| Input | Expected |
|-------|----------|
| `/d357 joe 1:1` | Launches meet.py in bg, writes state.json with pid, confirms |
| `/d357 joe 1:1` (while one is running) | Aborts with "already recording" |
| `/d357 stop` | SIGTERM, waits for filing, reports path, clears state |
| `/d357 stop` (nothing running) | Reports "No recording active." |
| `/d357` (nothing running) | Auto-detects calendar event name, starts recording |
| `/d357` (while running) | Reports current recording status |
| `/d357 standup --no-teams` | Launches mic-only (in-person) |
