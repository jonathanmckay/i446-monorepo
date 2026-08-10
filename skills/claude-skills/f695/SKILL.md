---
name: "f695"
description: "File a pasted i9 weekly update (hpm) into the f695 tracking doc, with optional comma-delimited comments and grade. Usage: /f695 <Person Name>: <pasted hpm>[, <comments>, <grade>]"
user-invocable: true
---

# i9 Weekly Update Intake (/f695)

Outlook has no API access wired up in this environment, so i9 (Xbox) weekly
updates ("hpms") can't be auto-fetched the way m5x2's are. This skill is the
manual intake path: the user pastes the email content, the skill files it into

```
~/vault/h335/f693/1-f695-weekly-updates.md
```

The doc holds **writeups only** (the summary tables were removed 2026-08-10).
The visual grid — one column per person, ✓ per covered week, JM's grade beside
the ✓, "due <Friday>" for the current week, ✍️ when JM's own update is
mid-draft — is the **f695 dash** at `http://ix:5558/f695`, rendered by
`~/i446-monorepo/tools/m5x2-weekly-reports/build.py` from this doc's
`## Weekly updates` entries. The roster (column set and order) is `I9_PEOPLE`
in that script; Growth (JM) stays rightmost.

## Usage

```
/f695 <Person Name>: <pasted hpm>, <comments>, <grade>
```

Everything up to the **first colon** is the person's name; everything after
is comma-delimited into up to three parts, split on the **last two commas**
(the hpm itself can be long, multi-paragraph, and contain its own commas and
colons):

- **hpm** — the raw pasted weekly-update email body.
- **comments** *(optional)* — JM's own take on the update.
- **grade** *(optional)* — JM's A–F (with optional +/-) quality grade.

**Detection rule:** rsplit the post-colon text on `,` twice. If the final
piece (trimmed) matches `^[A-Fa-f][+-]?$`, treat the three pieces as
hpm / comments / grade. Otherwise the whole thing is just the hpm (no
comments or grade) — do NOT mangle an hpm that merely ends with commas.
Exception: when the trailing comma-parts are unmistakably JM's first-person
commentary on the update (not part of the pasted email), treat them as
comments even without a grade. A grade requires the comments slot too;
`/f695 Bei: <hpm>, B+` (one trailing comma-part that parses as a grade) is
grade-only with empty comments.

The name may be omitted when the update itself identifies the sender/team —
infer the person from content and the roster, and say so in the confirmation.

One person per invocation — if a digest email covers multiple people, run
`/f695` once per person with just their portion pasted in.

## Steps

1. **Get today's date** via `date` (don't trust conversation context blindly —
   shell out).

2. **Pick the "week of" the update COVERS — not today's week.** Entries file
   under the Sunday-anchored week the update reports on (i9 updates are due
   the **Friday** of the week they cover; a Friday send covers its own week):
   - Parse the covered range from the hpm ("Mon 8/3 to Fri 8/7", "week
     ending 8/7") → `week_start` of that range's start
     (`week_start = d - ((d.weekday() + 1) % 7)` days; Mon=0..Sun=6).
   - No explicit range and pasted Mon–Wed → the PREVIOUS Sunday-anchored
     week (it almost certainly reports last week).
   - No range, pasted Thu–Sun → the current week.
   Format as `Y.MM.DD` (e.g. `2026.08.02`).

3. **Read** `~/vault/h335/f693/1-f695-weekly-updates.md`.

4. **Append the full content** under `## Weekly updates`:
   - Find (or create, keeping newest-week-first order) the
     `### Week of <Y.MM.DD>` heading for the covered week.
   - Under it, add `#### <Person Name>` — the name must exactly match the
     dash roster spelling (`I9_PEOPLE` in build.py); JM's own update files as
     `#### Growth (JM)`. If the update arrived AFTER its Friday due date
     (e.g. pasted the following Monday for last week without having been sent
     Friday), write `#### <Person Name> (late)` — the dash renders a ⚠️
     beside the ✓.
   - If the pasted hpm naturally breaks into the doc's existing categories
     (Shipped/Blocked/Next/Decisions, or the Working/Stuck style of recent
     entries), extract and reformat into those bullets. If it doesn't fit
     cleanly, don't force it — use a single `**Update:**` block rather than
     fabricating structure that isn't there.
   - When comments and/or a grade were given, close the entry with:

     ```
     **JM (<grade>):** <comments>
     ```

     (omit the parenthetical when there's no grade; omit the whole line when
     there's neither). The dash parses the grade from exactly this line —
     never write the grade anywhere else.

5. **Save the file**, then rebuild the dash so the ✓/grade shows up now
   rather than at the next cron run:

   ```bash
   python3 ~/i446-monorepo/tools/m5x2-weekly-reports/build.py
   ```

   (Needs the Google token for the m5x2 half; if it fails, say so — the 6:40
   cron on Ix will catch up.)

## Response Style

One line:
```
f695 → <Person> logged (week of <Y.MM.DD>[, graded <grade>])
```

If the person isn't in the dash roster (`I9_PEOPLE`), file the entry anyway
and tell the user the dash won't show it until the roster is updated.
