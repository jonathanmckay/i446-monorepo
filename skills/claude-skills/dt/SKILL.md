---
name: "dt"
description: "Use when the user invokes /dt or asks to claim a dtd task and switch the Toggl timer to it. Not for claim-only /d or timer-only /tg."
user-invocable: true
---

# /dt: claim a task and start its timer

Follow `/d` for task selection, creation, optional requests, and completion.
The difference is that a successful claim also switches Toggl to that exact
task, using its full name, project mapping, and task-id link. A timer already
running for that task stays running without resetting its start.

If a `[claim hook]` line says the task was claimed and the timer started or
was already running, do not repeat either operation. If it says `Timer failed`,
the claim still succeeded: report the timer failure, not success.

When no hook ran, pass the arguments verbatim as one argument:

```bash
# Example: claim dtd code wh and start that task's timer.
python3 ~/code/i446/tools/did/agent_claims.py dt "wh"
```

In Copilot, set `CLAUDE_CODE_SESSION_ID` to the current Copilot session UUID
for this command if that environment variable is absent.

Bare `/dt` follows bare `/d`: choose the task matching this thread's actual
deliverable, then run `agent_claims.py dt "<id>"`. If no task fits, run
`agent_claims.py dt "new <thread goal> (N) [N] @code"`, with estimates and
label inferred as in `/todo`.

For ambiguity, ask which candidate the user means before claiming or changing
the timer; run `agent_claims.py dt "<chosen id>"` after selection. A stale
code or missing row claims nothing and leaves the timer unchanged.

`/dt off`, `/dt release`, and `/dt done` follow `/d` without changing the
timer. They do not start a timer named after the control command.

Confirm the claim and timer in one line. If a request followed the colon,
continue with that request after the claim and timer succeed.
