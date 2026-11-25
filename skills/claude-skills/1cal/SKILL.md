---
name: "1cal"
description: "Use when JM runs /1cal, opens the weekly '1 cal' card, or asks to clean up his calendars: the same invite showing on both 个人 (jbm) and m5x2 Cal, or doubled time-record entries. Not for creating, moving or rescheduling an event (calendar MCP), not for the daily time record (/0r), not for Toggl gaps (/2n, /-1l), not for the meal plan (/mealplan)."
user-invocable: true
---

# Weekly calendar cleanup (/1cal)

The `1 cal` weekly card. Today it does one thing: finds duplicate events on
JM's two calendars and removes the ones he picks. The card has more to it than
that, so this skill does not close it; JM completes `1 cal` in dtd.

| Name | Calendar id | What lives there |
|---|---|---|
| 个人 (jbm) | `jonathan.b.mckay@gmail.com` | Personal events, plus the `/0r` time-record archive |
| m5x2 Cal | `mckay@m5c7.com` | McKay Capital |

Both logins in the calendar MCP's `tokens.json` (`jbm` and `m5c7`) are the
same account, mckay@m5c7.com, which reaches 个人 as a shared owner. Nothing
can act as the gmail account itself.

```
/1cal                       # report: 60 days back to 90 ahead
/1cal 2026-10-01 2026-12-31 # another window
```

## Steps

1. **report.** Read-only; saves the numbered plan to `~/.local/state/jm/1cal.json`.
   ```bash
   python3 ~/i446-monorepo/tools/1cal/1cal.py [--from YYYY-MM-DD --to YYYY-MM-DD]
   ```
   Show its output as it stands. Two sections:
   - **Invites on both calendars**: one invite (same iCalUID) on 个人 and on
     m5x2 Cal. Each line says which copy it would keep and why, and is marked
     `auto` or `manual`.
   - **Time-record doubles**: two archive events on 个人 for one Toggl entry.
     The nightly `0r` copy is kept.
   `No duplicates.` → say so and stop.
2. **ask.** JM picks by number. Wait for his answer; the report is not
   approval, and a calendar change is visible to other guests.
3. **apply.** Only the items he named, from the saved plan:
   ```bash
   python3 ~/i446-monorepo/tools/1cal/1cal.py --apply "1,3,5=m5x2,records"
   ```
   | Token | Meaning |
   |---|---|
   | `N` | Item N, suggested side |
   | `N=jbm` / `N=m5x2` | Item N, keep that calendar's copy instead |
   | `records` | Every time-record double |
   | `all` | Everything marked `auto` |
   Echo each result line. A plan from an earlier day is refused: rerun the report.
4. **manual items.** Never work around a `manual` line with the calendar MCP.
   Tell JM what it is and let him decide:
   - He organizes the event on the calendar he wants to drop: the event has to
     be moved to the other calendar, which this skill does not do.
   - A repeating invite with only some instances on both calendars.
   - His other address is not on the guest list, so the copy has another source.

## Which copy is kept

| Organizer | Kept | How the other copy goes |
|---|---|---|
| One of JM's addresses | The organizer's calendar | That event's guest list loses his other address (`mckay@m5x2.com` counts as m5x2) |
| Someone at m5c7.com or m5x2.com | m5x2 Cal | The 个人 copy is deleted |
| Anyone else | 个人 | The m5x2 copy is deleted |

No guest is notified (`sendUpdates=none`). Each event is appended as it was to
`~/.config/1cal/removed.jsonl` before it changes; Google keeps deleted events
in the calendar's trash for 30 days.

## Report

After an apply, one line per item plus:

```
1cal: 3 invites cleaned, 20 time-record doubles removed, 1 left manual.
```
