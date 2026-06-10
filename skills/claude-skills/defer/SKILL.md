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

If the user specified additional changes beyond the date and points, apply them after defer-fast.py completes:

- `rename: <new name>` → use `update-tasks` to change the future stub's content
- `p1`/`p2`/`p3`/`p4` → use `update-tasks` to change priority on the future stub
- Additional notes → use `update-tasks` to append to description

The future stub's task ID is in the JSON output at `.stubs.future`.

### Step 5: Report

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
