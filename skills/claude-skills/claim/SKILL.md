---
name: "claim"
description: "Tie this Claude session to a dtd task: dtd moves it to the top with a live spinner while you work. Short form: /d (and /d new <task> creates one first). Works as a decorator: /claim <task>: <request> claims the task, then does the request. Usage: /claim <task>[: request] | /claim off"
user-invocable: true
---

# /claim — link this session to a dtd task

## Parse

- `/claim off` (or `release`) → release, then stop. `/claim done` → mark done (✅, below), then stop.
- Otherwise split on the FIRST `:` → `<task query>` and an optional `<request>`.
  No colon: the whole argument is the task query and there is no request.

## Run

The UserPromptSubmit hook claims `/claim` and `/d` prompts before you start; if
your context has a `[claim hook]` line, follow it (see `/d`'s SKILL.md) instead
of re-running the claim.


```bash
python3 ~/i446-monorepo/tools/did/agent_claims.py claim "<task query>"
python3 ~/i446-monorepo/tools/did/agent_claims.py release
```

Output is one JSON line:
- `ok: true` → claimed (`task`, `task_id`). One task per session: claiming a
  new one drops the old one (unless `claim --also`).
- `ambiguous: [...]` (exit 3) → show the candidates numbered, one line each,
  and ask which. On the answer, rerun `claim` with that candidate's `id`.
- `error` (exit 2) → no match; say so and stop. Don't guess a task.

## Then

- With a `<request>`: confirm in one line (`😈 claimed: <task>`), then do the
  request as if the user had typed it alone. The claim is the decorator, not
  the task.
- Without one: just the confirmation line.

## When you think the claimed work is done (2026-10-22)

As soon as you (or a subagent you launched) believe a claimed task is finished,
mark it so dtd shows ✅ on the left of the card until JM verifies and completes it:

```bash
python3 ~/i446-monorepo/tools/did/agent_claims.py done            # every task this session holds
python3 ~/i446-monorepo/tools/did/agent_claims.py done <task id>  # just one
```

Never complete the Todoist task yourself; ✅ means "agent thinks it's done",
JM's ⌥↵ is the real close. Fanning work out to subagents? Claim each task they
own with `agent_claims.py claim --also "<task>"` (keeps this session's other
claims) and mark each `done` when its agent reports back finished.

## How it works (for reference, don't narrate)

Claims live in `~/vault/z_ibx/agent-claims/`. Hooks in `~/.claude/settings.json`
run `scripts/agent-claim-hook.sh`: UserPromptSubmit → working, Stop → idle,
SessionEnd → release. dtd greys working tasks with 😈 and spins a 😈 on its top
line; idle tasks look normal.
