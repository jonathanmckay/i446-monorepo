---
name: "grp"
description: "Use when JM runs /grp, asks to break an outcome into parts, to group or cluster related tasks under one outcome, or names a task with several distinct steps ('find tutor for Theo and Ren', 'get appraisal, buy units, close l912'). Also when a stalled-group card asks to delegate. Not for a goal repeated on several days (that is a quota, /1g), not for a single-step task (/todo), not for recurring cards."
user-invocable: true
---

# /grp: an outcome and its inputs

A group is a Todoist parent task (the outcome) with ONE level of subtasks
(its inputs). dtd shows it as one block: the 📦 outcome row, then its due
inputs indented beneath it, densest first. The
rules are in `~/i446-monorepo/lib/dtd_groups.py` and
`~/vault/g245/task-style-guide.md` (Groups); this skill is how a group gets
made and how the agent takes its share of the work.

```
/grp <outcome or existing task words>
/grp <outcome>: <context, links, what is already done>
```

## 1. The outcome (parent)

Find the existing task (Todoist `find-tasks`) or create it:

- verb phrase, `(N) [N]`, one domain label **plus `grp`**, due the day work
  can start (today by default), never recurring
- `[N]` is JM's value for the whole outcome. Use the one he gave or the one
  already on the task. Only when there is none, estimate as `/todo` does and
  say so. **Never lower or raise an existing `[N]`.**
- an outcome with two different finish lines is two groups (l912: "close at
  Ticor" and "in service by year end"), each with its own `[N]`

## 2. The inputs (subtasks)

List the concrete steps that are known now, 3 to 7 of them. For each:

| Field | Rule |
|---|---|
| name | verb phrase naming the residual human action, `(N) [N]` |
| `(N)` | JM's minutes only, after the agent has done its part |
| `[N]` | priced by what the step unlocks, at least `(N)`. A step that gates the whole outcome is worth more than its five minutes suggest |
| due | the day it can start. Today for anything ready. A step that needs another to finish first gets a later day, or is not created until that one closes |
| parent | the outcome (`parentId`) |
| description | links and the context needed to start, per the style guide |

The inputs must sum to **less** than the parent's `[N]`: the rest covers
steps nobody has found yet and the close. Do not subtract the inputs from the
parent; did-fast lowers the parent when each input is paid.

Tasks that already exist for this outcome are moved under the parent with
`update-tasks` (`parentId` only; send no project or section id). Do not
recreate them.

No deadlines. A date that matters is a fact for the description and a reason
to price the input higher.

## 3. Owners

| Label | Meaning | In dtd |
|---|---|---|
| none | JM does it | shown |
| `agent` | an agent can finish it without JM: read, check, compare, draft, file | dimmed with 🤖, last in the block |
| `waiting` | the next move is someone else's; name them in the task, due = follow-up day | shown with 🔔 on that day |

Give `agent` only to a step you are about to do in this session or hand to a
background agent now. A step left on `agent` with nobody working it sits
dimmed at the bottom of its block until someone notices.

## 4. Do the agent inputs

For each `agent` input, do the work, then hand JM the short remainder:

1. Put the result where he will act on it: the draft in the thread, the
   finding and links in the input's description.
2. Rename the input to the human step that is left
   ("send broker withdrawal, draft in thread (2) [30]") and shrink `(N)`.
3. Remove the `agent` label so the input shows in dtd.

If the work cannot be finished, say why in the description and remove the
`agent` label anyway. **Never complete an input or the parent yourself**:
completing pays 分 and lowers the parent, and that is JM's ⌥↵.

## 5. Refresh and report

```bash
python3 ~/i446-monorepo/tools/did/did-fast.py --refresh-cache
```

Report in this shape and stop:

```
📦 <outcome> [<N>]  (inputs <sum>, reserve <N - sum>)
  <input> (N) [N]  <owner, or the date it starts>
  ...
dtd shows: the outcome with <count> inputs due today beneath it
```

## Example

`/grp find tutor for Theo and Ren [120] @xk87`

```
📦 find tutor for Theo and Ren (60) [120]  (inputs 70, reserve 50)
  pick 2 of the 5 shortlisted tutors, list in description (5) [30]   JM, today
  book trial lessons with the 2 picked (5) [20]                     waiting, created when the pick closes
  confirm schedule with Louisa (5) [20]                             JM, today
dtd shows: the outcome with 2 inputs due today beneath it
```

The shortlist was an `agent` input: the agent searched, wrote five
candidates with rates into the description, renamed the input to the pick,
and removed the label.

## When the stall card arrives

`😈 stalled groups and repeat deferrals` (one card, from `tools/did/group_stall.py`) lists in its
description every outcome whose parent has not moved for 7 days, and every
one-off task deferred three times that is due again. It is a decision, not a
reminder. For each outcome listed, ask JM for one of three,
then act on it:

- **re-value**: he gives a new `[N]` for the parent or an input; edit it
- **delegate**: rerun steps 2 to 4 with more of the work on `agent`
- **drop**: close nothing; delete the parent (its inputs go with it) only on
  his explicit word

"Keep as is" is a fourth answer: he closes the card and the outcome is left
alone for another 7 days.
