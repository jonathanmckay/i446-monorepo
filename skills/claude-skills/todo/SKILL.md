---
name: "todo"
description: "Quick-add a task to Todoist due today. Usage: /todo <task> (time) [value] @tag"
user-invocable: true
---

# Quick Add Todoist Task (/todo)

Add a task to Todoist, due today, with optional time estimate, value, and tags.

## Response Style

**Minimal output.** Confirm in one line:
```
+ <task content> (N) [N] @tag → <project>
```

Do NOT explain what you're doing. Do NOT ask for confirmation. Just execute.

## Usage

```
/todo <task> (time) [value] @tag1 @tag2
```

- `<task>` — the task description (required)
- `(N)` — estimated time in minutes (optional)
- `[N]` — estimated value/points (optional)
- `@tag` — one or more tags/labels (optional)

All three modifiers are optional and can appear in any order within the input.

## Parsing

1. Extract all `@word` tokens → these become Todoist labels. Strip them from the task content.
2. Extract `(N)` where N is a number → this is the time estimate in minutes. Strip from content.
3. Extract `[N]` where N is a number → this is the value. Keep `[N]` in the task content (Todoist task name includes it for the /did flow).
4. Everything remaining (trimmed) is the task description.

## Inference

If the user omits any of the three modifiers, infer them:

### Time `(N)`
Estimate based on the task description:
- Quick actions (send a message, check something, review a doc): 5-15 min
- Medium tasks (write something, prep for a meeting, fill out a form): 20-40 min
- Large tasks (deep work, build something, research): 60-120 min

### Value `[N]`
Estimate based on impact:
- Low-value/routine (admin, inbox, chores): 3-5
- Medium-value (advancing a project, connecting with someone): 8-15
- High-value (strategic, unblocking, critical path): 20-40

### Tags `@tag`
Infer from the task content using domain mappings:
- Work/GitHub/Microsoft/coding → `i9`
- Real estate/property/tenant/rental/AppFolio → `m5x2`
- Finance/investing/taxes → `qz12`
- Family/kids/Theo/Ren/Aurora → `xk87`
- Social/friends/dinner out → `xk88` or `s897`
- Health/exercise/gym/food → `hcb`
- Media/reading/news/YouTube → `hcmc`
- Goals/planning/review → `g245`
- Infrastructure/admin/tooling → `i447`
- Home/cooking/cleaning/errands → `家`

## Creating the Task

Use the Todoist MCP `add-tasks` tool with:
- `content`: task description with `(N)` for time and `[N]` for value baked into the name
  - Format: `<description> (N) [N]`
- `dueString`: `"today"`
- `priority`: `"p4"` (default, unless the task sounds urgent/critical → `p1`)
- `labels`: the parsed/inferred tags
- `duration`: convert `(N)` minutes to duration format (e.g., `"30m"`, `"2h"`)

## Examples

**Fully specified:**
```
/todo review PR for auth changes (20) [15] @i9
→ + review PR for auth changes (20) [15] @i9
```

**Partially specified (infer missing):**
```
/todo call dentist about Theo appointment
→ + call dentist about Theo appointment (10) [5] @xk87
```

**Minimal:**
```
/todo fix the leaky faucet
→ + fix the leaky faucet (30) [8] @家
```

## Project Assignment

Tasks go to the **Inbox** by default. Do not assign a project — labels handle categorization.
