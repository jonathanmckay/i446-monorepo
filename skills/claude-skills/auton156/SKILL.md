---
name: "auton156"
description: "Use when JM runs /auton156, or asks to fill, backfill or reconstruct untracked Toggl time from logs ('fill in 0612-0631', 'what did I do while I was vibing', 'log my gaps'). Takes today's gaps or the ranges he names. Not for starting or stopping a timer (/tg, /do), not for the monthly coverage audit (/2n), not for ranges he has already described himself (/tg)."
user-invocable: true
---

# Fill untracked time (/auton156)

JM names a time he thinks you can work out. Find what he did, write it the way
he writes time entries, and log it. Do not show a draft and wait: the ask is
the approval, and an entry is one `toggl_delete` from gone.

```
/auton156                         # every gap of 5+ min today, up to now
/auton156 0612-0631 0633-0638     # just these ranges
/auton156 yesterday 2100-2130     # another day
```

## Steps

1. Run `date`. Read the day with `toggl_date` (or `toggl_today`). The ranges to
   fill are the named ones, or every untracked gap of 5+ minutes. Clip each to
   the free time: never overlap an entry that exists.
2. Gather evidence for those ranges:
   ```bash
   python3 ~/i446-monorepo/tools/auton156/evidence.py 0612-0631 0633-0638 [--date YYYY-MM-DD]
   ```
   Add `find-activity` from Todoist (`initiatorId` from `user-info`, the
   day's range, limit 100) for tasks added, deferred, renamed and completed.
   For a long hole, also check the calendar for a meeting.
3. Write the entries by the rules below and create each one with
   `toggl_create_entry`. Not `toggl_batch`: its shorthand splits on commas, and
   these descriptions have commas.
4. Report the table of what was logged, plus any range left empty.

## Entry rules

- **Description: 3-30 characters**, lowercase shorthand, like JM's own entries:
  `neon cutover plan`, `leo packet, rename bug`. Name the work, not the
  mechanics. No sentences, no "(inferred)".
- **One entry per 5-10 minutes.** Merge the small actions in a stretch into one
  entry. A 1-minute card close is part of the entry around it, never its own.
- **Times at 1-minute granularity**, HH:MM. Consecutive entries share a
  boundary.
- **Every entry gets a project**, from the task's Todoist label first, then the
  toggl_server domain mapping. A mixed stretch takes the project of most of
  its minutes.
- **Only JM's own actions count.** A request he typed into a session is one.
  Agent writes and automated sessions are not: `evidence.py` drops the ones it
  can recognize, so skip any others you spot.
- **Reading time counts.** A quiet stretch that ends in his reply to a report
  belongs to that work.
- **No evidence, no entry.** A range with nothing on either side stays empty and
  is named in the report. Never pad it with a neighbouring entry.

## Report

```
| Time | Entry | Project |
|---|---|---|
| 06:12-06:22 | slack, neon report | i447 |
| 06:22-06:31 | neon cutover plan | i447 |

Left empty: 07:10-07:25, nothing logged.
```

Add one line for any boundary that was a guess. Stop there.
