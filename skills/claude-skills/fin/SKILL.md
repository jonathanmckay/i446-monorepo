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
  `⏱ <timer>` when a timer was stopped. Then the final report, then step 2.
- `"status": "deferred"` → `⏭ <task> → <date>` per task (+ `⏱` line). Then the
  final report, then step 2.
- `"status": "delayed"` → `⏰ <task> hidden until HH:MM` per task (+ `⏱`). Then
  the final report, then step 2.
- `"status": "failed"` → show `error` or each failing task's `result` and
  stop. Do NOT tell JM to clear; the context is needed to fix it. A time that
  has already passed today is a failure, not a no-op.
- `"status": "no-claim"` → this session holds no claim. Bare /fin behaves
  like `/d off` on an unclaimed session (2026-11-14): say `no claim held`, touch
  nothing (no /did, no timer, no Todoist), then step 2. With an argument: say
  there is nothing to delay and stop; /defer handles a named task.

The reverse also holds (2026-11-14): closing a claimed task anywhere else
(dtd ⌥↵, /did) types `/clear` into the session that held it, once that
session is idle (agent_claims.fin_holders + the Stop hook). Only this
session's own /fin is exempt, which is why step 2 exists.

## 1b. Final report (2026-11-14, layout 2026-11-22)

Each task carries a `report` object (tools/did/fin_report.py). Render one block
per task inside a code fence, exactly this shape (JM, 2026-11-22: the old
one-line-per-field block was hard to parse; points first, then turns, then
cost):

```
📋 <task name, annotations stripped>
   60 分 · 23 turns · ≈ $14.20

   Done
   • <what was accomplished, 2–4 bullets>
   • <...>

   You     48m on the timer, 2 entries
   Model   1h52m active · 103 calls
   Tokens  92k new in · 38k out
   Thread  context avg 257k · 15.9M re-read
```

- **Headline** (line 2) is the three numbers JM reads first, in this order,
  joined by ` · `: `report.points` as `N 分` (never `$`; on a defer/snooze
  `0 分 (deferred)`), `report.model.turns` (his prompts), and
  `report.model.cost_usd` as `≈ $N.NN`. The cost is the API-equivalent list
  price (JM is on a Max plan: what it would have cost, not a charge); do not
  spell that out in the line. `cost_usd: null` → `cost n/a`.
- **Done** is yours to write: what the thread actually produced, from this
  conversation plus `report.notes` (the 😈 turn summaries posted to the task,
  which cover turns that have since left your context; they are machine
  summaries, so trust the conversation where the two disagree). Concrete
  outcomes and where they live (commits, files, cards), not activity. 2–4
  bullets, each one line, no trailing punctuation needed. A deferred/snoozed
  task says what is done so far and what remains.
- **You** is `report.jm.minutes` / `report.jm.entries` (Toggl time on this task
  since the claim). `0m` → `no timer on this task`.
- **Model** is `report.model`, counted from this task's claim to the session's
  next claim (2026-11-15: windows no longer overlap, so several tasks' reports
  add up to the session): `active_minutes`, then `messages` as "calls" (append
  `+ N subagent` when `subagent_calls` > 0). Unclaimed work between two claims
  is counted on the earlier task.
- **Tokens** is the work figure: `new_in` as "new in" (how much the context
  grew: prompts, tool results and files read) and `tokens.output` as "out".
- **Thread** is thread length, not work: `context_avg` and `tokens.cache_read`
  as "re-read" (the context re-sent on every call).
- Numbers: k/M, at most one decimal, drop a trailing `.0`. Labels are padded
  to one column so the values line up; blank lines exactly as shown.
- A `report` with `error`, or missing → print the block with `n/a` fields; never
  skip the report or stop on it.

## 2. Exit or clear

A skill cannot run /exit or /clear, and typing them into this session's own
terminal is blocked by the auto-mode classifier (tmux self-drive). After the
report, end with exactly one of:

- `→ closing this tab` when the JSON has `"tab_close": true` (this thread's
  cmux tab is driven from another host, normally Straylight). Nothing for JM
  to type (2026-11-22: "/fin should both stop the thread on ix and close the
  cmux tab on straylight"). done_session left two things: `fin-close/<tmux
  session>` for the launchers and `by-session/<sid>.exit` for the Stop hook
  (`scripts/agent-claim-hook.sh idle`), which types `/exit` about a second
  after this turn ends, once nothing is running in the background. The Ix
  launcher then lets the tmux session end, and Straylight closes the tab,
  always (2026-11-22: it used to start a fresh thread when this was the
  workspace's only Claude tab; JM: "I want /fin to close the entire CMUX
  tab"). So make this the last line and start nothing in the background
  after the script: a live background agent or shell holds the exit until it
  finishes, and any new prompt from JM cancels it.
- `→ /clear` otherwise (a local or phone session keeps its tab), so JM can
  type it.

The transcript stays in ~/.claude/projects/ and /resume brings it back.
