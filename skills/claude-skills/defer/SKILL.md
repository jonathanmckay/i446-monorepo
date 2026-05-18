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

### Step 3: Reschedule or split (recurring vs non-recurring)

Check if the task is recurring: look for `due.is_recurring == true` in the task data.

#### 3a: Non-recurring tasks

Use the Todoist REST API to reschedule. Use `due_date` (ISO) rather than `due_string` to avoid ambiguity:

```bash
curl -s -X POST "https://api.todoist.com/api/v1/tasks/TASK_ID" \
  -H "Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5" \
  -H "Content-Type: application/json" \
  -d '{"due_date": "YYYY-MM-DD"}'
```

Then proceed to Step 4 (apply changes), Step 5 (posthoc record), Step 6 (report).

#### 3b: Recurring tasks (split into two stubs)

For recurring tasks, do NOT reschedule (that destroys recurrence). Instead:

1. **Close the recurring task** (advances it to its next natural occurrence):
   ```bash
   curl -s -X POST "https://api.todoist.com/api/v1/tasks/TASK_ID/close" \
     -H "Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5"
   ```

2. **Parse points from the original task content.** Extract `[N]` from the content (e.g., `"0g (4) [8]"` has total points = 8). The user may specify partial points claimed (default: 5). Remaining = total - claimed.

3. **Create stub A (today, completed)** for partial credit claimed today:
   ```
   content: "deferred: <original task name> (5) [CLAIMED_PTS]"
   labels: ["posthoc"] + original labels
   project_id: original project
   due_date: today
   ```
   Then immediately close it.

4. **Create stub B (target date, open)** for remaining work:
   ```
   content: "<original task name> (N) [REMAINING_PTS]"
   labels: original labels (no "posthoc")
   project_id: original project
   due_date: target date (default: tomorrow)
   ```
   This stays open so it appears on the target day's task list.

5. Skip Steps 4 and 5 (changes and posthoc are handled inline above). Go to Step 6.

**Defaults:**
- Partial points claimed today: 5 (override with `[N]` in args)
- Target date: tomorrow (override with `<new-date>` arg)
- Duration on stub B: same `(N)` as original task

### Step 4: Apply changes (non-recurring only)

If the user specified changes, apply them in the same API call or a second call:

- `rename: <new name>` → update `content` field
- `[N]` in task name → update the `[N]` portion of the content string
- `p1`/`p2`/`p3`/`p4` → update `priority` field (p1=4, p2=3, p3=2, p4=1 in API... actually use string values directly: `"priority": 4` for p1 in REST API v1)
- Additional notes → update `description` field (append, don't overwrite)

**Priority mapping for REST API v1:** p1→`priority:4`, p2→`priority:3`, p3→`priority:2`, p4→`priority:1`

### Step 5: Create posthoc eval record (non-recurring only)

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

If recurring:
```
deferred (recurring): <task name> → closed + [CLAIMED] today / [REMAINING] on <new-date>
```

If changes were applied:
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

- Non-recurring tasks use `due_date` reschedule. Recurring tasks use the close+split-stubs flow to preserve recurrence.
- The posthoc record (non-recurring) uses `[0]` points since deferral itself has no output value.
- For recurring tasks, stub A gets `[CLAIMED]` points (default 5) and stub B gets `[REMAINING]` points. Both inherit the original task's labels and project.
- If the task has no project (inbox), use `"inbox"` as project_id.
