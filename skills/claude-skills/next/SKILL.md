---
name: "next"
description: "Show top 9 Todoist tasks for today, pick one to start a Toggl timer. Usage: /next"
user-invocable: true
---

# Next Up (/next)

Show today's top tasks and optionally start a Toggl timer for the chosen one.

## Cache

Cache file: `~/.claude/skills/next/cache.json`

The cache stores the last fetched task list so subsequent `/next` calls display instantly.

### Cache format

```json
{
  "fetched_at": "2026-04-19T10:30:00",
  "tasks": [
    {"n": 1, "content": "task name", "priority": "p1", "label": "qz12", "toggl_desc": "task name stripped", "todoist_id": "abc123"},
    ...
  ]
}
```

- `toggl_desc` = content with `(N)`, `[N]`, and `**` stripped out.
- `label` = first Todoist label matching the domain table below (or null).

## Flow

1. **Read cache.** Read `~/.claude/skills/next/cache.json` via the Read tool.
2. **Display immediately.** If the cache exists, display the task list from it right away. Format:
   ```
   N. [pX] <content> — <label>
   ```
   Append a note with the cache age: `(cached Xm ago)` or `(cached Xh ago)`.
3. **Ask the user to pick** a number (1–9) or "skip" to exit. Use AskUserQuestion.
4. **If user picks a task:**
   - Use `toggl_desc` and `label` from the cached entry.
   - Start a Toggl timer via the CLI: `python3 ~/i446-monorepo/mcp/toggl_server/toggl_cli.py start <description> [project_code]`
   - Confirm in one line: `Started: <description> → <project>`
5. **If user skips:** Output `No task selected.` and exit.
6. **Refresh cache in background.** After displaying (whether user picks or skips), fetch fresh tasks from Todoist MCP `find-tasks-by-date` and overwrite `cache.json`. This keeps it warm for next time.

### If no cache exists (first run)

Fetch from Todoist MCP `find-tasks-by-date` first, write the cache, then display and prompt as above.

## Domain Label → Toggl Project Mapping

Use the task's Todoist label to determine the Toggl project code:

| Label | Toggl project |
|-------|---------------|
| i9 | i9 |
| m5x2 | m5x2 |
| qz12 | qz12 |
| hcb | hcb |
| hcbp | hcbp |
| hcm | hcm |
| hcmc | hcmc |
| xk87 | xk87 |
| xk88 | xk88 |
| s897 | s897 |
| g245 | g245 |
| i447 | i447 |
| 家 | 家 |
| epcn | epcn |
| h335 | h335 |
| infra | infra |
| n156 | n156 |
| i444 | i444 |

If a task has multiple labels, use the first one that matches this table. If no label matches, omit the project (timer runs unassigned).

## Tools

- **Todoist:** `find-tasks-by-date` MCP tool (date: today)
- **Toggl:** CLI at `~/i446-monorepo/mcp/toggl_server/toggl_cli.py`
- **User input:** `AskUserQuestion` tool

## Response Style

**Minimal.** No preamble before the list. No explanation after. Just the numbered list, then the prompt, then the one-line confirmation.
