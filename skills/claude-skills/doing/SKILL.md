---
name: "doing"
description: "Add a task to Todoist and start a Toggl timer in one command. Combines /todo and /tg. Usage: /doing <task> (time) [value] @tag"
user-invocable: true
---

# Start Doing (/doing)

Add a task to Todoist (due today) and start a Toggl timer for it in a single command.

## Response Style

**Minimal output.** Two lines max:
```
+ <task content> (N) [N] @tag
Started: <description> → <project>
```

Do NOT explain. Do NOT ask for confirmation. Just execute.

## Usage

```
/doing <task> (time) [value] @tag1 @tag2
```

Same syntax as `/todo`. All modifiers are optional and inferred if missing.

## Steps

### 1. Create the Todoist task

Follow the exact same parsing, inference, and creation logic as `/todo`:

- Extract `@tag` tokens → Todoist labels. Strip from content.
- Extract `(N)` → time estimate in minutes. Strip from content.
- Extract `[N]` → value/points. Keep `[N]` in the task content.
- Infer any missing modifiers (time, value, tag) per `/todo` rules.
- Create via Todoist MCP `add-tasks` with `dueString: "today"`.
- Refresh cache in background: `python3 ~/i446-monorepo/tools/did/did-fast.py --refresh-cache &>/dev/null &`

### 2. Start the Toggl timer

Run `tg-fast.py` with the task description and the resolved project (from the `@tag`):

```bash
python3 ~/i446-monorepo/tools/tg/tg-fast.py "<description> @<tag>"
```

Use the same `@tag` that was assigned to the Todoist task so the Toggl project matches.

### 3. Report

```
+ <task> (N) [N] @tag
Started: <description> → <project>
```

## Examples

**Fully specified:**
```
/doing review Forza data deck (30) [15] @i9
→ + review Forza data deck (30) [15] @i9
  Started: review Forza data deck → i9
```

**Inferred:**
```
/doing draft email to Drew about carports
→ + draft email to Drew about carports (15) [8] @m5x2
  Started: draft email to Drew about carports → m5x2
```

**With existing shortcode:**
```
/doing fix the /did defer bug (30) [20] @i447
→ + fix the /did defer bug (30) [20] @i447
  Started: fix the /did defer bug → i447
```
