---
name: "do"
description: "Start an activity. Variable tasks (xk20, 冥想, bball, ...) start an input-based timer with points auto-calculated from duration on /done. Anything else creates a Todoist task + starts a timer, and requires [N] points (asks if missing). Usage: /do <task> [(time)] [[points]] [@tag]"
user-invocable: true
---

# Start Activity (/do)

One entry point for starting work. Two modes, decided by the task name:

1. **Variable mode** — the task is in the Variable Task Set below → input-based timer; points auto-calculated from duration when stopped via `/done` or `/did`.
2. **Task mode** — anything else → create a Todoist task (due today) + start a Toggl timer, like the old `/doing`. **`[N]` points are required in this mode** — if the input has no `[N]`, ask the user for the point value before doing anything else.

`/doing` is an alias for this skill.

## Mode decision

Check the input (case-insensitive, after stripping `(N)`, `[N]`, `@tag` annotations) against the Variable Task Set:

**0n (daily):** xk20, xk22, xk26, xk88, 冥想, o314, 其他人, hiit, bball

**1n+ (weekly):** s897, 家 (alias for family), relax, s+hcbp

Match → Variable mode. No match → Task mode.

## Variable mode

1. **Resolve stale session.** If `~/.claude/skills/do/active.json` exists from a previous `/do`, resolve it first:
   - Read `active.json` to get old task name and `started_at`.
   - Check if the old timer is still running via `toggl_cli.py current`. If yes, stop it and parse duration.
   - If already stopped, compute duration as `now - started_at` (conservative fallback).
   - Run `python3 $DID_FAST "<old_task> <duration_minutes>"` to write points and close Todoist.
   - Delete `active.json`.
   - Report: `Resolved /do: <old_task> → <N>min`

2. **Resolve Toggl project** using the same shortcode mapping as /tg:
   - xk20, xk22, xk26 → xk87
   - xk88 → xk88
   - 冥想 → hcm
   - o314 → hcm
   - 其他人 → hcm
   - s897 → s897
   - 家 → 家
   - relax → hcb
   - s+hcbp → hcbp

3. **Stop any running timer** first:
   ```bash
   python3 ~/i446-monorepo/mcp/toggl_server/toggl_cli.py stop
   ```

4. **Start the timer:**
   ```bash
   python3 ~/i446-monorepo/mcp/toggl_server/toggl_cli.py start "<task>" <project>
   ```

5. **Save active session** to `~/.claude/skills/do/active.json`:
   ```json
   {
     "task": "<task name as entered>",
     "resolved_name": "<resolved name, e.g. family for 家>",
     "type": "0n or 1n+",
     "started_at": "<ISO 8601 timestamp>",
     "project": "<toggl project code>"
   }
   ```

6. **Update tg cache** at `~/.claude/skills/tg/cache.json`:
   ```json
   {"running": {"desc": "<task>", "project": "<project>"}}
   ```

7. **Report:**
   ```
   Started: <task> → <project> (variable)
   ```

### Variable completion flow

When `/done` or `/did` (no args) is called, the /did skill detects the active /do session and:
1. Stops the timer
2. Reads duration from the stopped timer
3. Uses duration (minutes) as points
4. Runs `/did <task> <minutes>` to write to Neon and close Todoist
5. Clears active.json

This is handled by /did, not by /do. The /do skill only starts the session.

## Task mode

1. **Require points.** Parse `[N]` from the input. If absent, ask: `points for "<task>"?` and wait for the answer. Do NOT infer or default points in this mode; the ask is intentional (this differs from /todo's inference).

2. **Create the Todoist task** following the same parsing, inference, and creation logic as `/todo` for everything except points:
   - Extract `@tag` tokens → Todoist labels. Strip from content.
   - Extract `(N)` → time estimate in minutes. Strip from content; infer if missing.
   - Keep `[N]` in the task content.
   - Infer missing `@tag` per /todo domain rules.
   - Create via Todoist MCP `add-tasks` with `dueString: "today"`.
   - Refresh cache in background: `python3 ~/i446-monorepo/tools/did/did-fast.py --refresh-cache &>/dev/null &`

3. **Start the Toggl timer** with the matching project:
   ```bash
   python3 ~/i446-monorepo/tools/tg/tg-fast.py "<description> @<tag>"
   ```

4. **Report:**
   ```
   + <task> (N) [N] @tag
   Started: <description> → <project>
   ```

## Response Style

Minimal, same as /tg. One line for variable mode, two lines for task mode. The only question ever asked is the missing-points prompt in task mode.

## Examples

```
/do bball
→ Started: bball → hcbp (variable)

/do review Forza data deck (30) [15] @i9
→ + review Forza data deck (30) [15] @i9
  Started: review Forza data deck → i9

/do draft email to Drew about carports
→ points for "draft email to Drew about carports"?
  (user: 8)
→ + draft email to Drew about carports (15) [8] @m5x2
  Started: draft email to Drew about carports → m5x2
```
