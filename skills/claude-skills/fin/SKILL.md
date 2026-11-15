---
name: "fin"
description: "Use when JM types /fin to finish with the task this session claimed: bare /fin closes it (points, Todoist, timer, then hand off to /clear); /fin N (a bare integer, days) delays it N days instead; /fin 1500, /fin 15:00 or /fin 3pm hides it in dtd until that time today. Replaces /done (2026-11-14). Not for marking a task agent-finished without closing (/d done), not for other tasks (/defer, /did)."
user-invocable: true
---

# Fin (/fin)

JM typing /fin IS the real decision about his claimed task (his ⌥↵, or his
ctrl-d / ctrl-v), so acting on the Todoist task here is intended; the /d rule
"never complete it yourself" is about agents deciding on their own.

## 1. Run the session script with the argument as typed

```bash
python3 ~/i446-monorepo/tools/did/done_session.py            # close
python3 ~/i446-monorepo/tools/did/done_session.py 3          # delay 3 days
python3 ~/i446-monorepo/tools/did/done_session.py 15:00      # hide until 15:00 today
```

Pass JM's argument verbatim; the script decides. A bare integer is DAYS
(`/fin 15` is fifteen days, not 15:00); a time needs four digits, a colon or
am/pm (`1500`, `15:00`, `3pm`, `3:30pm`). Anything else is a usage error.

It finds this session's claims (held or ✅-marked via `agent_claims.py done`)
and, per argument:

- **close** — each task runs through `did-fast.py --task-id`, the same path as
  dtd's ⌥↵: points to 0分, Todoist closed, build order ticked, claim cleared,
  the running Toggl timer stopped only if it is that task.
- **days** — each task runs through `defer-fast.py --id <id> <N>`, the same
  path as dtd's ctrl-d (one-off copy on the target date for a recurring card,
  a [0] posthoc audit stub today, no credit). Claim cleared, timer stopped
  only if it is that task.
- **time** — each task id is written into dtd's ctrl-v snooze file as an
  absolute timestamp (plus the Syncthing mirror), so dtd cli, dtd web and the
  other host hide the row until then and show it again automatically. Nothing
  touches Todoist. Claim cleared, timer stopped only if it is that task.

Read its JSON:

- `"status": "done"` → one line per task: `✓ <task> → <result>`, plus
  `⏱ <timer>` when a timer was stopped. Then step 2.
- `"status": "deferred"` → `⏭ <task> → <date>` per task (+ `⏱` line). Then step 2.
- `"status": "delayed"` → `⏰ <task> hidden until HH:MM` per task (+ `⏱`). Then step 2.
- `"status": "failed"` → show `error` or each failing task's `result` and
  stop. Do NOT tell JM to clear; the context is needed to fix it. A time that
  has already passed today is a failure, not a no-op.
- `"status": "no-claim"` → this session never claimed a task. Bare /fin:
  invoke `/did` with no arguments (stops the running timer and completes the
  task its description names), then step 2. With an argument: say there is
  nothing to delay and stop; /defer handles a named task.

## 2. Clear

A skill cannot run /clear, and typing it into this session's own terminal is
blocked by the auto-mode classifier (tmux self-drive). End with exactly:

`→ /clear`

so JM can type it. The transcript stays in ~/.claude/projects/ and /resume
brings it back.
