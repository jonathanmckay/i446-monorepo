---
name: "0g"
description: "Set or sync daily goals (0₲). With args: writes goals to build order + Todoist. Without args: syncs existing build order goals to Todoist. Usage: /0g [goals]"
user-invocable: true
---

# Daily Goals (/0g)

Manage daily goals in the `## 0₲` section of the build order file and sync to Todoist.

## Files

- **Build order**: `~/vault/g245/-1₦ , 0₦ - Neon {Build Order}.md`
- **Section**: `## 0₲` (stop at the next `##` or `###` heading)
- **Todoist project**: `0g` (ID: `6XfvCQ3p8Gq6fhGR`)
- **Todoist labels**: `#关键径路` on every task

## Notation Parsing

Goals may include these inline annotations:

| Syntax | Meaning | Maps to |
|--------|---------|---------|
| `(N)` | Expected time in minutes | Todoist `duration` |
| `[N]` | Points for the domain (`@project`) | Store in task text; use when logging completion |
| `@code` | Domain/project (e.g. `@i9`, `@m5x2`) | Todoist label (strip from content) |
| `{N}` | Bonus 0g points → 0分 Z column on completion | Store in task text as `{N}` |

Strip `@code` from the displayed task content in Todoist (it becomes a label). Keep `(N)`, `[N]`, and `{N}` in the task content as-is — they are read by `/did` and other skills at completion time.

## Behavior

### With arguments — set new goals

Goals are provided as arguments after `/0g`. May be bullet lines, comma-separated, or free text (one goal per sentence/line).

**Step 1: Parse goals**

Each line/item becomes one goal. Strip leading `-`, `*`, `[ ]`, `[x]` markers. Parse and extract:
- `(N)` → duration in minutes
- `[N]` → point value
- `@code` → domain label (remove from content)
- `{N}` → bonus points (keep in content)

**Step 2: Update build order**

Read the build order file. Find `## 0₲`. Replace the existing goal items (lines matching `  - [ ]` or `  - [x]`) with the new goals formatted as:
```
  - [ ] <goal content>
```
Preserve the `### 以后的目标` subsection and everything below it. Only replace the items between `## 0₲` and the next subsection/heading.

**Step 3: Add to Todoist**

For each goal, create a Todoist task:
- **Content**: goal text (with `(N)`, `[N]`, `{N}` preserved; `@code` stripped)
- **Project**: `0g` (ID `6XfvCQ3p8Gq6fhGR`)
- **Labels**: `["#关键径路", "#0g"]` + the `@code` label if present (e.g. `"i9"`)
- **Priority**: `p1`
- **Due**: today
- **Duration**: from `(N)` if present

**Step 4: Confirm**

```
0g → N goals set + synced to todoist
```

---

### Without arguments — sync existing goals to Todoist

**Step 1: Read build order**

Read the build order file. Extract all unchecked items (`- [ ]`) under `## 0₲` (before any `###` subsection).

If none found, report "No 0g goals in build order" and stop.

**Step 2: Add to Todoist**

For each unchecked goal, create a Todoist task (same format as above — parse annotations from the text).

**Step 3: Confirm**

```
0g → N goals synced to todoist
```

## Response Style

Minimal. Confirm in one line. Do NOT explain. Do NOT ask for confirmation.
