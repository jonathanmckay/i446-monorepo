---
name: "auton156"
description: "Use when JM runs /auton156, or asks to fill, backfill or reconstruct untracked Toggl time from logs ('fill in 0612-0631', 'what did I do while I was vibing', 'log my gaps'). Takes today's gaps or the ranges he names. Not for starting or stopping a timer (/tg, /do), not for the monthly coverage audit (/2n), not for closing the 😈 -1l block card (/-1l, which runs this first), not for ranges he has already described himself (/tg)."
user-invocable: true
---

# Fill untracked time (/auton156)

JM names a time he thinks you can work out. Find what he did, write it the way
he writes time entries, and log it. Do not show a draft and wait: the ask is
the approval, and an entry is one `toggl_delete` from gone.

```
/auton156                         # every hole of 5+ min today, up to now
/auton156 0612-0631 0633-0638     # just these ranges
/auton156 yesterday 2100-2130     # another day
```

## Steps

1. **find_ranges.** Run `date`. Named ranges: read the day with `toggl_date`
   (or `toggl_today`) and clip each to the free time. No ranges: the script
   finds the holes itself (5m or more, the same threshold janus flashes at; midnight to now):
   ```bash
   python3 ~/i446-monorepo/tools/auton156/evidence.py [--date YYYY-MM-DD]             # holes + evidence
   python3 ~/i446-monorepo/tools/auton156/evidence.py 0612-0631 0633-0638 [--date ..]  # named ranges
   ```
   Each `hole HH:MM-HH:MM Nm after:.. before:.. [placeholders:id,..]` line is
   a range to fill; the event lines under them are the local evidence.
   `no holes` or `no data`: say so and stop. Never overlap an entry that
   exists; a hole with `placeholders:` is filled by `toggl_delete` on those
   ids first.
2. **gather_evidence**, in one batch with step 1's output in hand:
   - Tasks: Todoist `find-activity` (`objectType` task, `initiatorId` from
     `user-info`, the day's range, limit 100) for tasks added, deferred,
     renamed and completed.
   - Calendar: `list-events` for the day on every account. Keep events JM
     accepted or organized that overlap a range. Drop all-day events,
     declined ones, and holds with no other attendee unless a log backs
     them. If the call fails, say `calendar unavailable` in the report.
3. **fill.** Per range, the first source that applies wins:

   | Evidence in the range | Entry |
   |---|---|
   | Calendar event JM attended | The event's time clipped to the range, title as shorthand (`1:1 dana`, `kn47 standup`), project from the calendar account and attendees |
   | Completed tasks | Work backward from each completion time by the task's `(N)` estimate, clipped to the range and to the previous evidence. Project from the task's label |
   | cc prompts or neon writes | By the entry rules below |
   | 睡觉 on one side, nothing inside | No entry. Report it as `sleep?` with the times |
   | Nothing | No entry |

   A meeting outranks prompts typed during it. Evidence covering part of a
   range fills that part only. Create each entry with `toggl_create_entry`.
   Not `toggl_batch`: its shorthand splits on commas, and these descriptions
   have commas.
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
| Time | Entry | Project | From |
|---|---|---|---|
| 06:12-06:22 | slack, neon report | i447 | cc |
| 06:22-06:31 | neon cutover plan | i447 | cc |
| 09:05-09:35 | kn47 standup | m5x2 | cal |

Left empty: 05:40-06:10 sleep? (睡觉 ends 05:40), 07:10-07:25 nothing logged.
```

Add one line for any boundary that was a guess. Stop there.
