---
name: "d357"
description: "Record a meeting as a transcript. Launches meet.py in the background; stop with /d357 stop to finalize transcription and file notes to vault/d357/. Usage: /d357 <meeting name> | /d357 stop | /d357 status"
user-invocable: true
---

# Record Meeting (/d357)

Wraps `~/i446-monorepo/tools/meet/meet.py`:
- Records mic (add `--teams` for Teams/Zoom mixed audio)
- Transcribes locally with faster-whisper (base.en)
- Extracts notes + action items via Claude
- Files to `vault/d357/YYYY-MM-DD-<slug>.md`

## State

Background PID + metadata stored at `~/.claude/skills/d357/state.json`:
```json
{"pid": 12345, "name": "joe 1:1", "started": "2026-04-20T10:00:00", "log": "/tmp/d357-12345.log"}
```

Absent or `pid: null` → no recording active.

## Commands

### `/d357 <name>` — start recording

1. Check state.json; if a recording is running, **abort** and tell the user to stop it first.
2. Parse flags from the name: `--teams` for Teams/Zoom mode (mix system audio + mic).
3. Launch in background:
   ```bash
   cd ~/i446-monorepo/tools/meet && \
   nohup python3 meet.py "<name>" --domain d357 [--teams] > /tmp/d357-$$.log 2>&1 &
   echo $!
   ```
4. Write state.json with the returned PID, name, timestamp, log path.
5. Confirm in one line: `Recording → <name> (pid <pid>). /d357 stop when done.`

### `/d357 stop` — finalize

1. Read state.json. If no active PID, report `No recording active.` and exit.
2. Send SIGTERM: `kill -TERM <pid>`. meet.py has a signal handler that stops recording and proceeds to transcription + extraction + filing.
3. Poll `ps -p <pid>` every 2s until the process exits (transcription on base.en runs ~0.1× realtime, so a 30-min meeting takes ~3 min to transcribe).
4. Tail the log for the "📁 Filed:" line to get the output path.
5. Clear state.json (set `pid: null`).
6. Report: `Stopped. Filed → <path>`. If the log shows errors, surface them.

### `/d357 status` or `/d357` (no args) — show current state

Read state.json. If active, report `Recording: <name> since <HH:MM> (pid <pid>)`. If not, `No recording active.`

## Notes

- **Excel/OneDrive not required** — meet.py writes markdown to the vault directly.
- **Whisper model:** `base.en` (default, ~150MB download on first run). Override with `--model small.en` for better accuracy at 3× the time.
- **Teams mode requires one-time setup** (BlackHole virtual audio device); see the docstring at the top of meet.py.
- The `d357` domain maps to `vault/d357/` (new; created on first file).

## Regression tests

| Input | Expected |
|-------|----------|
| `/d357 joe 1:1` | Launches meet.py in bg, writes state.json with pid, confirms |
| `/d357 joe 1:1` (while one is running) | Aborts with "already recording" |
| `/d357 stop` | SIGTERM, waits for filing, reports path, clears state |
| `/d357 stop` (nothing running) | Reports "No recording active." |
| `/d357` | Reports current state (running or idle) |
| `/d357 standup --teams` | Launches with `--teams` flag |
