---
name: "do"
description: "Start a timer for input-based (variable-point) activities. Only works for designated variable tasks. When stopped via /done, points are auto-calculated from duration. Usage: /do <task>"
user-invocable: true
---

# Start Variable Activity (/do)

Start a Toggl timer for an input-based activity and save session state. When the timer is stopped (via `/done` or `/did`), duration is auto-converted to points and written to Neon + Todoist.

## Variable Task Set

Only these tasks are valid for /do:

**0n (daily):** xk20, xk22, xk26, xk88, 冥想, o314, 其他人

**1n+ (weekly):** s897, 家 (alias for family), relax, s+hcbp

If the input doesn't match one of these, reject with: `Not a variable task. Use /tg instead.`

## Execution

1. **Validate** the input is a known variable task (case-insensitive match against the sets above).

2. **Resolve stale session.** If `~/.claude/skills/do/active.json` exists from a previous `/do`, resolve it first:
   - Read `active.json` to get old task name and `started_at`.
   - Check if the old timer is still running via `toggl_cli.py current`. If yes, stop it and parse duration.
   - If already stopped, compute duration as `now - started_at` (conservative fallback).
   - Run `python3 $DID_FAST "<old_task> <duration_minutes>"` to write points and close Todoist.
   - Delete `active.json`.
   - Report: `Resolved /do: <old_task> → <N>min`

3. **Resolve Toggl project** using the same shortcode mapping as /tg:
   - xk20, xk22, xk26 → xk87
   - xk88 → xk88
   - 冥想 → hcm
   - o314 → hcm
   - 其他人 → hcm
   - s897 → s897
   - 家 → 家
   - relax → hcb
   - s+hcbp → hcbp

4. **Stop any running timer** first:
   ```bash
   python3 ~/i446-monorepo/mcp/toggl_server/toggl_cli.py stop
   ```

5. **Start the timer:**
   ```bash
   python3 ~/i446-monorepo/mcp/toggl_server/toggl_cli.py start "<task>" <project>
   ```

6. **Save active session** to `~/.claude/skills/do/active.json`:
   ```json
   {
     "task": "<task name as entered>",
     "resolved_name": "<resolved name, e.g. family for 家>",
     "type": "0n or 1n+",
     "started_at": "<ISO 8601 timestamp>",
     "project": "<toggl project code>"
   }
   ```

7. **Update tg cache** at `~/.claude/skills/tg/cache.json`:
   ```json
   {"running": {"desc": "<task>", "project": "<project>"}}
   ```

8. **Report:**
   ```
   Started: <task> → <project> (variable)
   ```

## Response Style

One line. Same as /tg. No explanation needed.

## Completion Flow

When `/done` or `/did` (no args) is called, the /did skill detects the active /do session and:
1. Stops the timer
2. Reads duration from the stopped timer
3. Uses duration (minutes) as points
4. Runs `/did <task> <minutes>` to write to Neon and close Todoist
5. Clears active.json

This is handled by /did, not by /do. The /do skill only starts the session.
