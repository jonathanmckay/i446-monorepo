---
name: "defer"
description: "Defer a Todoist task to a later date with optional edits, and log a posthoc eval record for today. Usage: /defer <task> <new-date> [changes]"
user-invocable: true
---

# Defer Task (/defer)

Reschedule a Todoist task to a later date, apply any inline changes, and create a closed posthoc task for today recording that the task was evaluated.

## Response Style

**Minimal output.** Confirm in one line:
```
deferred: <task> → <new-date> [+ changes]
```

Do NOT explain what you're doing. Do NOT ask for confirmation unless multiple tasks match.

## Usage

```
/defer <task> <new-date> [changes]
```

- `<task>` — substring to search for in active Todoist tasks
- `<new-date>` — when to defer to. Natural language (`"next Monday"`, `"4/15"`, `"tomorrow"`, `"May 1"`) or ISO date (`"2026-05-01"`)
- `[changes]` — optional inline edits to apply at the same time:
  - `rename: <new name>` — change the task content
  - `+<N>pts` or `[N]` — update the value in the task name
  - `p1`/`p2`/`p3`/`p4` — change priority
  - Any other text is appended to description notes

## Steps

### Step 1: Find the task

Search all active Todoist tasks (not just 0neon) for content matching `<task>` (case-insensitive, substring):

```bash
curl -s "https://api.todoist.com/api/v1/tasks" \
  -H "Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5" | python3 -c "
import json, sys
tasks = json.load(sys.stdin)
q = 'QUERY'
matches = [t for t in tasks if q.lower() in t.get('content','').lower()]
for t in matches:
    print(t['id'], repr(t['content']), t.get('due',{}).get('date',''), t.get('labels',[]))
"
```

- If **0 matches**: report "task not found" and stop.
- If **1 match**: proceed.
- If **2–4 matches**: list them and ask the user which one. Stop and wait.
- If **5+ matches**: ask user to be more specific. Stop and wait.

### Step 2: Resolve the new date

Convert `<new-date>` to ISO format (`YYYY-MM-DD`) for use in the API. Examples:
- `"tomorrow"` → tomorrow's date
- `"4/15"` → `2026-04-15` (current year)
- `"next Monday"` → nearest upcoming Monday
- `"May 1"` → `2026-05-01`
- ISO passthrough: `"2026-05-01"` → `2026-05-01`

### Step 3: Reschedule the task

Use the Todoist REST API to reschedule. Use `due_string` for natural language, or `due_date` for ISO:

```bash
curl -s -X POST "https://api.todoist.com/api/v1/tasks/TASK_ID" \
  -H "Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5" \
  -H "Content-Type: application/json" \
  -d '{"due_date": "YYYY-MM-DD"}'
```

**Important:** Use `due_date` (ISO) rather than `due_string` when you've already resolved the date, to avoid ambiguity.

### Step 4: Apply changes (if any)

If the user specified changes, apply them in the same API call or a second call:

- `rename: <new name>` → update `content` field
- `[N]` in task name → update the `[N]` portion of the content string
- `p1`/`p2`/`p3`/`p4` → update `priority` field (p1=4, p2=3, p3=2, p4=1 in API... actually use string values directly: `"priority": 4` for p1 in REST API v1)
- Additional notes → update `description` field (append, don't overwrite)

**Priority mapping for REST API v1:** p1→`priority:4`, p2→`priority:3`, p3→`priority:2`, p4→`priority:1`

### Step 5: Create posthoc eval record for today

Create a Todoist task representing "I evaluated this task today and chose to defer it," then immediately close it:

**Content format:**
```
deferred: <original task name> → <new-date> (5) [0]
```

**Fields:**
- `labels`: `["posthoc"]` + the original task's labels (carry them over for domain context)
- `project_id`: same project as the original task
- `due_date`: today in ISO format (`2026-04-05`)

```bash
# Create
curl -s -X POST "https://api.todoist.com/api/v1/tasks" \
  -H "Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5" \
  -H "Content-Type: application/json" \
  -d '{"content": "deferred: TASK_NAME → NEW_DATE (5) [0]", "labels": LABELS, "project_id": "PROJECT_ID", "due_date": "2026-04-05"}'

# Immediately close
curl -s -X POST "https://api.todoist.com/api/v1/tasks/NEW_TASK_ID/close" \
  -H "Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5"
```

### Step 6: Report

```
deferred: <task name> → <new-date>
```

If changes were applied, append them:
```
deferred: <task name> → <new-date> [renamed / p2 / ...]
```

## Examples

```
/defer get a birth certificate for aurora 5/15
→ deferred: get a birth certificate for aurora (30) [80] → 2026-05-15

/defer aurora insurance 4/20 p2
→ deferred: insurance for Aurora → 2026-04-20 [p2]

/defer swim lessons next Monday rename: research swim lesson providers
→ deferred: check in on summer swim lessons strategy → 2026-04-13 [renamed]
```

## Notes

- Always use `reschedule` semantics (due_date) — do NOT use update-tasks with due_string on recurring tasks as it destroys recurrence. If the task is recurring, warn the user before rescheduling.
- The posthoc record uses `[0]` points since deferral itself has no output value — it's just an eval log.
- If the task has no project (inbox), use `"inbox"` as project_id.
