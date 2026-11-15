---
name: "-1l"
description: "Use when JM runs /-1l, opens the 😈 -1l block card, or asks to sweep today's time log for holes ('fill my holes', 'log the day so far', 'what is missing from Toggl today'). Covers midnight to now. Not for ranges he names himself (/auton156), not for a finished day (/0t --check, /0t fill), not for the monthly audit (/2n), not for starting or stopping a timer (/tg, /do)."
user-invocable: true
---

# Block log sweep (/-1l)

Find every hole in today's Toggl from 00:00 to now and fill the ones the
record can explain: calendar, completed tasks, cc logs. Do not show a draft
and wait: the ask is the approval, and an entry is one `toggl_delete` from
gone. Entry shape and the "only JM's own actions" rule are `/auton156`'s
**Entry rules**; read them there, they are not restated here.

```
/-1l              # today, 00:00 to now
/-1l dry          # report the holes and proposed fills, write nothing
```

## Steps

1. **find_holes.** Run `date`. Read `toggl_today`. A hole is untracked time of
   more than 5 minutes between 00:00 and now (a running timer counts as
   tracked up to now). No holes: say so and go to step 5.
2. **gather_evidence**, all three in one batch:
   - Calendar: `list-events` for today on every account. Keep events JM
     accepted or organized that overlap a hole. Drop all-day events, declined
     ones, and holds with no other attendee unless a log backs them.
   - Tasks: Todoist `find-activity` (`objectType` task, `eventType` completed,
     `initiatorId` from `user-info`, today's range, limit 100).
   - cc logs, with every hole as a range:
     ```bash
     python3 ~/i446-monorepo/tools/auton156/evidence.py 0612-0631 0905-0950
     ```
3. **fill.** Per hole, first source that applies wins:

   | Evidence in the hole | Entry |
   |---|---|
   | Calendar event JM attended | The event's time clipped to the hole, title as shorthand (`1:1 dana`, `kn47 standup`), project from the calendar account and attendees |
   | Completed tasks | Work backward from each completion time by the task's `(N)` estimate, clipped to the hole and to the previous evidence. Project from the task's label |
   | cc prompts or neon writes | As `/auton156` |
   | 睡觉 on one side, nothing inside | No entry. Report it as `sleep?` with the times |
   | Nothing | No entry |

   A meeting outranks prompts typed during it. Evidence covering part of a
   hole fills that part only. Create each entry with `toggl_create_entry`
   (never `toggl_batch`, never across midnight, never overlapping an entry).
4. **check_tasks_pointed.** From the step 2 completions, list any real task
   (not a habit, not `@posthoc`) closed in the previous 2-hour block with no
   `[N]` or `{N}`. This is the card's own criterion.
5. **close_card.** Only when no hole over 5m remains in the previous block and
   step 4 found nothing:
   ```bash
   python3 ~/i446-monorepo/tools/did/did-fast.py --ritual -1l >/dev/null 2>&1
   python3 ~/i446-monorepo/tools/did/did-fast.py --refresh-cache
   ```
   Otherwise leave the card open and say which condition failed. Never on
   `dry`. Skip it outside 04:00-22:00 (no block is open) and when the
   step 2 completions already show this block's `😈 -1l` closed.

## Report

```
| Time | Entry | Project | From |
|---|---|---|---|
| 09:05-09:35 | kn47 standup | m5x2 | cal |
| 09:35-09:50 | leo packet | m5x2 | task |
| 13:10-13:22 | neon cutover plan | i447 | cc |

Left empty: 05:40-06:10 sleep? (睡觉 ends 05:40), 15:02-15:20 nothing logged.
Unpointed: none.
😈 -1l closed.
```

One line for any boundary that was a guess. Stop there.
