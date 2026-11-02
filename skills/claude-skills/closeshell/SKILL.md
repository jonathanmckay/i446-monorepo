---
name: "closeshell"
description: "Use when JM is about to close this terminal, tab or laptop, says /closeshell, 'wrap up', 'hand this off' or 'I'm closing this', and the thread's work must survive into dtd. Not for marking work finished (that is /d done) and not for a plain /todo."
user-invocable: true
---

# Close this shell without losing the work (/closeshell)

Closing a tab no longer kills the process (cc runs on ix, or in a local
tmux), but the *state* of the thread lives only in this conversation. This
skill writes it into dtd: a 😈 hand-off note on the claimed card, and one
card per open follow-up, so the next session (`/d <task>` on ix) or JM can
finish them.

Helper: `python3 ~/i446-monorepo/tools/did/closeshell.py` (every command
prints one JSON line). Claims: `~/i446-monorepo/tools/did/agent_claims.py`.

## Steps

1. **Where am I.** `closeshell.py status` → `survives_close` and the claims
   this session holds. If `survives_close` is false and a turn is still
   running (background agents, a long command), say so first: closing now
   ends it. Otherwise nothing is at risk on the process side.

2. **The claimed card(s).** For each claim in `status.claims`, decide from
   the conversation:
   - finished → `agent_claims.py done <task id>` (✅; JM's ⌥↵ is the close).
   - unfinished → first `closeshell.py handoff <task id> "<note>"`, then
     `agent_claims.py release`. The note is first person, three sentences
     at most: what is done, what is left, where the files are (paths,
     branch, commit). It is the whole context the next session gets.

3. **Follow-ups.** List the open follow-ups this thread produced: things
   JM asked for and were deferred, defects found but not fixed, "next"
   items from your own replies. Skip anything already in dtd (check
   `status.claims` and the surfaced list from the /d hook). For each:
   ```bash
   python3 ~/i446-monorepo/tools/did/closeshell.py followup \
     "wire /closeshell into janus (20) [8] @i447" \
     --note "Straylight's dtd got the Enter→/todo branch in 68579eaf; janus.py's input box has no equivalent yet. Start at tools/tg/janus.py:on_submit."
   ```
   Estimate `(N)` `[N]` `@code` as /todo does. The card lands at the top of
   dtd with 🕐 and the note as its first 😈 comment. Zero follow-ups is a
   fine answer; never invent one.

4. **Confirm in a few lines**, nothing more:
   ```
   🔒 closeshell · session on ix (tmux cc-dtd-todo): safe to close
   ✅ 1-i446 #14: neon-rules mismatch P/S/X
   🕐 wire /closeshell into janus (20) [8] @i447
   ```
   Then stop. Do not start new work after this; the hand-off is the last
   thing the session writes, so the Stop hook's turn note matches it.

## Rules

- Never complete a Todoist task here; ✅ means "agent thinks done".
- Never claim the follow-ups to this session as working: a dying session's
  😈 would spin until STALE_SECS. `followup` claims and releases so the row
  sits in the claimed tier (🕐) with no live spinner.
- One note per card, written once. If JM keeps typing after /closeshell,
  the next /closeshell writes a new note; it does not edit the old one.
