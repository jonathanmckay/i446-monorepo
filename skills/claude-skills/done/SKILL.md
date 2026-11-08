---
name: "done"
description: "Close the task this session claimed (dtd/Todoist + points), stop its Toggl timer if it is the running one, then hand off to /clear. No claim: stop the running timer and /did its description. Usage: /done"
user-invocable: true
---

# Done (/done)

JM typing /done IS the real close (his ⌥↵), so completing the Todoist task
here is intended; the /d rule "never complete it yourself" is about agents
deciding on their own.

## 1. Close the session's task

```bash
python3 ~/i446-monorepo/tools/did/done_session.py
```

It finds this session's claims (held or ✅-marked via `agent_claims.py done`)
and runs each through `did-fast.py --task-id`, the same path as dtd's ⌥↵:
points to 0分, Todoist closed, build order ticked, claim cleared, and the
running Toggl timer stopped only if it is that task. Read its JSON:

- `"status": "done"` → report one line per task: `✓ <task> → <result>`, plus
  `⏱ <timer>` when a timer was stopped. Then step 2.
- `"status": "failed"` → show each failing task's `result` and stop. Do NOT
  tell JM to clear; the context is needed to fix it.
- `"status": "no-claim"` → this session never claimed a task. Fall back to the
  old /done: invoke `/did` with no arguments (stops the running timer and
  completes the task its description names). If that succeeds, step 2.

## 2. Clear

A skill cannot run /clear, and typing it into this session's own terminal is
blocked by the auto-mode classifier (tmux self-drive). End with exactly:

`→ /clear`

so JM can type it. The transcript stays in ~/.claude/projects/ and /resume
brings it back.
