---
name: "d"
description: "Tie this session to a dtd task (→dtd): moves it to the top of dtd with a live spinner while you work, then does the request. No match in dtd = it creates the task first, then claims it. Bare /d picks (or creates) the task for what this thread is accomplishing. Usage: /d [<task>[: request]] | /d new <task>[: request] | /d off"
user-invocable: true
---

# /d — attach this session to a dtd task (short form of /claim)

- `/d <task>[: request]` claims the matching dtd task; if NOTHING matches it
  creates `<task>` in Todoist (due today) and claims that (default since
  2026-10-06).
- `/d new <task>[: request]` always creates `<task>` in Todoist (due today; an `@code`
  token becomes its label), then claims it, so work that isn't in dtd yet
  still shows up there as underway.
- `/d off` releases.
- **Bare `/d`** (2026-10-19) attaches this session to the task it is *already*
  working on. The hook leaves a `[claim hook] bare /d ...` line listing the
  surfaced dtd tasks with ids. Then:
  1. State the **thread goal**: the deliverable this whole conversation is
     producing, not the latest message. One line, ≤ 8 words, names the output
     ("fix -1n points lost to hung Excel", "cc-pick phone labels").
  2. Pick the surfaced task that IS that work (same deliverable, not merely the
     same domain). If one fits →
     `python3 ~/i446-monorepo/tools/did/agent_claims.py claim <id>`.
  3. If none fits → create it from the thread goal, with estimates and label
     inferred exactly as /todo does:
     `python3 ~/i446-monorepo/tools/did/agent_claims.py new "<thread goal> (N) [N] @code"`.
  4. Confirm in one line: `😈 claimed: <task>` or `created and 😈 claimed: <task>`.
  Never ask which task; never claim a look-alike just because it shares a domain.

The UserPromptSubmit hook (`scripts/agent-claim-hook.sh` → `agent_claims.py hook`)
already did the work before you saw this prompt, and left one line in your
context starting with `[claim hook]`:

- `😈 claimed: <task>` or `created and 😈 claimed: <task>` → done. Do NOT run
  `agent_claims.py claim`/`new` again or create the task again.
- `released ...` → done; just confirm.
- `ambiguous ... Candidates: <id>: <task>; ...` → show them numbered, ask which,
  then run `python3 ~/i446-monorepo/tools/did/agent_claims.py claim <id>`.
- `no dtd task matches ...` → only from an old hook; run
  `python3 ~/i446-monorepo/tools/did/agent_claims.py new "<task>"` yourself.
- `couldn't create ...` → say so and stop.
- No `[claim hook]` line at all (hook didn't run) → do it yourself: split the
  argument on the FIRST `:`, then
  `python3 ~/i446-monorepo/tools/did/agent_claims.py claim "<query>"`,
  `... new "<task>"`, or `... release`.

**A bare `[N]` while a task is claimed is that task's VALUE, not a completion.**
The hook writes `[N]` onto the claimed task (`set value [N] ...`). Never log
points or run /did for it: points are opportunity until JM completes the task in
dtd (⌥↵). If no hook line appears, do the same by hand (edit the task content),
still without logging.

Then: with a `<request>` after the colon, confirm in one line
(`😈 claimed: <task>`) and do the request as if the user had typed it alone.
Without one, just the confirmation line.
