---
name: "c"
description: "Short form of /claim: tie this session to a dtd task (moves it to the top of dtd with a live spinner while you work), then do the request. Usage: /c <task>[: request] | /c off"
user-invocable: true
---

# /c — claim a dtd task (short form of /claim)

The UserPromptSubmit hook (`scripts/agent-claim-hook.sh` → `agent_claims.py hook`)
already ran the claim before you saw this prompt, and left one line in your
context starting with `[claim hook]`:

- `😈 claimed: <task>` → done. Do NOT run `agent_claims.py claim` again.
- `released ...` → done; just confirm.
- `ambiguous ... Candidates: <id>: <task>; ...` → show them numbered, ask which,
  then run `python3 ~/i446-monorepo/tools/did/agent_claims.py claim <id>`.
- `no dtd task matches ...` → say so and stop. Don't guess a task.
- No `[claim hook]` line at all (hook didn't run) → fall back to /claim's steps:
  `python3 ~/i446-monorepo/tools/did/agent_claims.py claim "<task query>"`
  (split the argument on the FIRST `:`; `off` → `... release`).

Then: with a `<request>` after the colon, confirm in one line
(`😈 claimed: <task>`) and do the request as if the user had typed it alone.
Without one, just the confirmation line.
