---
name: "doing"
description: "Alias for /do. Variable tasks start an input-based timer; anything else creates a Todoist task + timer and requires [N] points (asks if missing). Usage: /doing <task> [(time)] [[points]] [@tag]"
user-invocable: true
---

# Start Doing (/doing) — alias of /do

`/doing` and `/do` were combined (2026-06-06). Follow the instructions in `~/.claude/skills/do/SKILL.md` exactly, treating the input identically:

- Task in the Variable Task Set → variable-mode timer (points from duration via /done)
- Anything else → Todoist task + Toggl timer; `[N]` points required, ask if missing
