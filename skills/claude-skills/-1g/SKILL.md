---
name: -1g
description: Set goals for the current 2-hour block. Writes to build order and syncs to Todoist with #关键径路. Usage: /-1g [next] <goals>
user-invocable: true
---

# Set 2-Hour Block Goals (/-1g)

Set goals for the current (or next) 2-hour time block. Goals go into the `-1₲` section of the build order file and into Todoist with `#关键径路` + `#-1g` labels.

## Arguments

```
/-1g [next] <goals as bullet points or free text>
```

- If `next` appears as the first word, target the NEXT time block instead of the current one.
- Goals can be provided as bullet lines, comma-separated, or free text (one goal per line/item).

## Time Block Mapping

Auto-detect from wall clock time (America/Los_Angeles):

| Block | Local Time | Arabic |
|-------|-----------|--------|
| 0 | 05:00-06:59 | فجر |
| 1 | 07:00-08:59 | شروق |
| 2 | 09:00-10:59 | صباح |
| 3 | 11:00-12:59 | ظهر |
| 4 | 13:00-14:59 | عصر |
| 5 | 15:00-16:59 | آصيل |
| 6 | 17:00-18:59 | غروب |
| 7 | 19:00-20:59 | غسق |
| 8 | 21:00-22:59 | زلة |

Times outside 05:00-22:59 default to فجر (block 0).

## Files

- **Build order**: `~/vault/g245/-1₦ , 0₦ - Neon {Build Order}.md`
- **Section**: `## -1₲` — goals go under the matching Arabic time heading
- **Todoist project**: `0g` (ID: `6XfvCQ3p8Gq6fhGR`)
- **Todoist labels**: `#关键径路` AND `#-1g` on every task

## Steps

### Step 1: Determine time block

Get current local time (America/Los_Angeles). Compute which block using `(hour - 5) // 2`, clamped to 0-8. If `next` argument is present, add 1 (clamped to 8).

### Step 2: Parse goals

Extract goal items from the user's input. Each line starting with `-` or `*` or a numbered list is one goal. If no list markers, split by newlines. Strip checkbox syntax if present (e.g., `- [ ] foo` becomes `foo`). Preserve `{N}` minute annotations.

### Step 3: Update build order markdown

Read the build order file. Find the `## -1₲` section. Under the target Arabic time heading (e.g., `- شروق`), replace any existing indented items with the new goals as `    - [ ] <goal>` lines (4-space indent).

Keep all other time blocks untouched.

### Step 4: Create Todoist tasks

For each goal, create a Todoist task using the Todoist MCP `add-tasks` tool:
- **Content**: the goal text
- **Project**: `0g` (ID: `6XfvCQ3p8Gq6fhGR`) — use project name "0g"
- **Labels**: `["#关键径路", "#-1g"]`
- **Priority**: `p1`
- **Due**: `today`
- **Duration**: from `{N}` annotation if present

### Step 5: Confirm

Output one line:
```
-1g → <Arabic block name> (<HH:MM>-<HH:MM>): N goals set
```

## Response Style

Minimal. Execute and confirm in one line. Do NOT ask for confirmation. Do NOT explain what you're doing.
