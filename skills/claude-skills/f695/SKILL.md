---
name: "f695"
description: "File a pasted i9 weekly update (hpm) into the f695 tracking doc, with optional comma-delimited comments and grade. Usage: /f695 <Person Name>: <pasted hpm>[, <comments>, <grade>]"
user-invocable: true
---

# i9 Weekly Update Intake (/f695)

Outlook has no API access wired up in this environment, so i9 (Xbox) weekly
updates ("hpms") can't be auto-fetched the way m5x2's are (see
`~/i446-monorepo/tools/m5x2-weekly-reports/`, a Gmail-driven HTML grid — that
tool is m5x2-only and out of scope here). This skill is the manual intake
path: the user pastes the email content, the skill files it into

```
~/vault/h335/f693/1-f695-weekly-updates.md
```

Nobody hand-edits that doc directly anymore — this skill is the only writer,
so the table's Last Update/Quality/Status/Notes stay in sync with what's
actually been pasted in.

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
- **grade** *(optional)* — JM's A/B/C/F (with optional +/-) quality grade.

**Detection rule:** rsplit the post-colon text on `,` twice. If the final
piece (trimmed) matches `^[A-Fa-f][+-]?$`, treat the three pieces as
hpm / comments / grade. Otherwise the whole thing is just the hpm (old-style
invocation, no comments or grade) — do NOT mangle an hpm that merely ends
with commas. A grade requires the comments slot too; `/f695 Bei: <hpm>, B+`
(one trailing comma-part that parses as a grade) is grade-only with empty
comments.

One person per invocation — if a digest email covers multiple people, run
`/f695` once per person with just their portion pasted in.

## Steps

1. **Get today's date** via `date` (don't trust conversation context blindly —
   shell out). Compute the Sunday-anchored "week of" date for the log entry:
   `week_start = today - ((today.weekday() + 1) % 7)` days (Python
   `datetime.date.weekday()`: Mon=0..Sun=6). Format both as `Y.MM.DD` to match
   the doc's existing style (e.g. `2026.08.04`).

2. **Read** `~/vault/h335/f693/1-f695-weekly-updates.md`.

3. **Find the person's row.** Search the five i9-side tables in order — `##
   i9 (Xbox)`, `## Growth`, `## DS`, `## Analytics Infra`, `## Product
   Infra` — for a row whose Person cell matches (case-insensitive, first or
   last name is enough: "Elliot" or "Silvers" both match "Elliot Silvers").
   - **Found:** note which section.
   - **Not found:** ask the user which of the 5 sections to file them under
     (the reorg is fresh — Growth/DS/Analytics Infra/Product Infra are still
     empty placeholder tables with no rows yet), then add a new row with
     their name in that section's table.

4. **Update the row** in place:
   - `Last Update` → today's `Y.MM.DD`
   - `Quality` → the parsed **grade**, verbatim (uppercased, keep any +/-),
     when one was given. When no grade was given, leave the cell untouched —
     never infer a grade from the content.
   - `Status` → infer from the content's tone: 🟢 on track / shipped /
     ahead of schedule; 🔴 blocked / escalation / critical risk named
     explicitly; 🟡 everything else (needs attention, mixed, unclear) —
     default to 🟡 rather than guess optimistically.
   - `Notes` → a compressed one-line summary (≤ ~20 words) of the single
     most important thing in the update. The summary is of the **hpm**, not
     of JM's comments.

5. **Append the full content** under `## Weekly updates`:
   - Find (or create, at the **top** of the section — newest week first,
     matching existing order) a `### Week of <Y.MM.DD>` heading for this
     week's Sunday-anchored date.
   - Under it, add `#### <Person Name>`.
   - If the pasted hpm naturally breaks into the doc's existing categories
     (see `## Update format`: Shipped/Blocked/Next/Decisions, or the
     Working/Stuck/Move-first/Time-sensitive style used in older entries),
     extract and reformat into those bullets. If it doesn't fit cleanly,
     don't force it — just include the pasted text under a single
     `**Update:**` paragraph rather than fabricating structure that isn't
     there.
   - When comments and/or a grade were given, close the entry with:

     ```
     **JM (<grade>):** <comments>
     ```

     (omit the parenthetical when there's no grade; omit the whole line when
     there's neither).

6. **Save the file.**

## Response Style

One line:
```
f695 → <Person> logged (<Section>, <date>, <status emoji>[, graded <grade>])
```

If the person was new and you had to ask which section, mention that inline
instead of asking twice on a future invocation for the same person.
