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
/defer <task> [when] [changes]
```

- `<task>` — substring to search for in active Todoist tasks
- `[when]` — how far to defer. **Defaults to 1 day** if omitted.
  - A bare integer `N` → **today + N days** (`/defer water plants 3` → 3 days out)
  - Natural language (`"next Monday"`, `"4/15"`, `"tomorrow"`, `"May 1"`) or ISO date (`"2026-05-01"`) → that absolute date
- `[changes]` — optional inline edits to apply at the same time:
  - `rename: <new name>` — change the task content
  - `+<N>pts` or `[N]` — update the value in the task name
  - `p1`/`p2`/`p3`/`p4` — change priority
  - Any other text is appended to description notes

## Recurring tasks

Deferring a recurring task defers **only the current occurrence**. The script
creates a standalone one-off copy on the target date, and the recurring parent
advances to its **own next scheduled occurrence with the recurrence preserved**
— the series cadence is never changed and nothing is marked complete. (The old
behavior silently stripped the recurrence; that's fixed.)

## Steps

### Step 1: Resolve `when`

Pass `when` straight through to the script when it's a **bare integer** (days)
or already ISO. Only convert natural language to ISO yourself first:
- `"tomorrow"` → tomorrow's date · `"4/15"` → `2026-04-15` · `"next Monday"` →
  nearest upcoming Monday · `"05.19"` → `2026-05-19`
- A bare number like `3` → pass `3` verbatim (script computes today + 3 days)
- Omitted → pass nothing (script defaults to today + 1 day)

### Step 2: Parse claimed points

If the user included `[N]` in the args, that's the claimed points for today's stub. Default: 5.

### Step 3: Run defer-fast.py

```bash
python3 ~/i446-monorepo/tools/did/defer-fast.py "<task_name>" "<days|YYYY-MM-DD>" <claimed_points>
```

The script handles everything: finding the task, detecting recurring vs non-recurring, creating the one-off copy / stubs, advancing the recurring parent. It outputs JSON with the result.

- If it exits 1 with `"error": "multiple matches"`, list the matches and ask the user which one.
- If it exits 1 with `"error": "task not found"`, report and stop.

### Step 4: Apply extra changes (if any)

If the user specified additional changes beyond the date and points, apply them after defer-fast.py completes, targeting the **deferred instance**:

- Non-recurring: the task itself, ID at `.stubs.future`.
- Recurring: the one-off copy, ID at `.stubs.deferred_copy` (do NOT edit the parent series at `.stubs.future`).

Then:
- `rename: <new name>` → use `update-tasks` to change that task's content
- `p1`/`p2`/`p3`/`p4` → use `update-tasks` to change its priority
- Additional notes → use `update-tasks` to append to its description

### Step 5: Report

```
deferred: <task name> → <new-date>
```

If recurring (note the series stays intact):
```
deferred (recurring): <task name> → one-off on <target>; series next <next_recurrence>
```

If changes were applied:
```
deferred: <task name> → <new-date> [renamed / p2 / ...]
```

## Examples

```
/defer water plants
→ deferred: water plants → 2026-06-11   (default: 1 day)

/defer water plants 3
→ deferred: water plants (10) [5] → 2026-06-13   (today + 3 days)

/defer get a birth certificate for aurora 5/15
→ deferred: get a birth certificate for aurora (30) [80] → 2026-05-15

/defer daily standup 2
→ deferred (recurring): daily standup → one-off on 2026-06-12; series next 2026-06-11
```

## Notes

- Non-recurring tasks reschedule in place via `due_date`.
- Recurring tasks defer **only the current occurrence**: a non-recurring one-off copy is created on the target date (inherits the original's labels, project, priority, and `(N)`/`[N]` content), and the parent advances to its own next occurrence with the recurrence string preserved (passing `due_string` keeps it recurring; a bare `due_date` write would strip it). Nothing is marked complete.
- The posthoc eval record is created for today with `[0]` points (deferral has no output value) and inherits the task's labels.
- If the task has no project (inbox), use `"inbox"` as project_id.
