---
name: "1i9"
description: "Weekly i9 (Microsoft/Xbox) review. Calculates last week's (Sun-Sat) i9 minutes from Toggl and i9 points from 0分, asks 3 reflection questions, writes everything to the Neon 'i9' sheet's row for that week, and completes the '1 i9' weekly habit. Usage: /1i9 [YYYY-MM-DD]"
user-invocable: true
---

# Weekly i9 Review (/1i9)

A lightweight weekly retrospective scoped to the **i9** domain (Microsoft /
Xbox work) — narrower than `/1s`'s cross-domain review. Computes last
week's tracked time and points, asks three reflection questions in-chat
(no separate TUI — this is a 3-field survey, not a multi-page form), writes
the row in the Neon `i9` sheet, and completes the existing `1 i9 (10) [40]`
weekly Todoist habit.

## Usage

```
/1i9 [YYYY-MM-DD]
```

- No arg — reviews the most recently completed **Sunday–Saturday** week
  (not Wed-Tue — that's `/1s897`'s convention, not this one).
- `YYYY-MM-DD` — treat that date as "today" for backfilling an older week
  (passed straight through to `week_calc.py`).

## The `i9` sheet (`Neon分v12.2.xlsx`)

Pre-populated for the whole year, one row per fiscal week, **never
appended to** — row 3 = fiscal week 1 (`1.1`), incrementing one row per
week. Columns (confirmed against live data 2026-09-15):

| Col | Meaning | Source |
|-----|---------|--------|
| A | M.W fiscal week label (e.g. `9.2`) | already populated — verify, don't write |
| B | Rating vs. expectations: `MM` (missed) / `MA` (met ambition) / `EE` (exceeded) | Question 1 |
| C | i9 points earned that week | computed from `0分` col R |
| D | What got done last week | Question 2 |
| E | What exceeding expectations looks like this week | Question 3 |
| F | i9 minutes tracked that week (Toggl) | computed — **new column, added by this skill** (previously unused; set a `min` label in row 2 the first time it's written) |

Rows 1–2 hold sparse/inconsistent header remnants (only B1=`i9`, B2=`OL` are
non-blank) — don't try to fully reconstruct a header row, just add the `F`
label defensively per Step 5.

The `MM`/`MA`/`EE` rating vocabulary is inferred from ~30 rows of existing
data (never seen elsewhere in the workbook as a documented enum) — if the
user ever corrects this reading, update this table.

## Steps

### Step 1: Resolve the target week and row

```bash
python3 ~/.claude/skills/1i9/week_calc.py [YYYY-MM-DD]
```

Pass the optional date arg through if given. Prints
`week_start<TAB>week_end<TAB>row<TAB>label` (ISO dates, 1-indexed sheet row,
expected M.W label). The label is informational only — **Step 4 must verify
it against the sheet's actual column A value before writing anything**; if
they don't match, stop and report rather than guessing.

### Step 2: Toggl minutes for the week

```
mcp__toggl_server__toggl_range  start_date=<week_start>  end_date=<week_end>
```

One call for the whole week (not 7 separate `toggl_date` calls — same
efficiency note as `/1s` Step 3). Sum the duration of every entry tagged
with project **i9** (id `209635316`) across all 7 days → `weekly_minutes`.

### Step 3: i9 points for the week

Read `0分` column **R** (i9) for each of the 7 dates in the week (col B,
`M/D` format) via `ix-osa.sh`, same pattern as `/1s` Step 4:

```applescript
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set s to sheet "0分" of wb
    set out to ""
    repeat with i from START_ROW to END_ROW
        set out to out & (string value of range ("B" & i) of s) & "|" & (string value of range ("R" & i) of s) & "\n"
    end repeat
    return out
end tell
```

Find `START_ROW`/`END_ROW` by scanning col B for `week_start`..`week_end`'s
`M/D` dates first. Sum the 7 values → `weekly_points`.

### Step 4: Find and verify the target row in the `i9` sheet

```applescript
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set s to sheet "i9" of wb
    return string value of cell 1 of row ROW_PLACEHOLDER of s
end tell
```

Substitute `ROW_PLACEHOLDER` with Step 1's `row`. Compare the returned
label to Step 1's `label`. **If they don't match, stop** — the fiscal
anchor may have drifted or the sheet was edited; report both values to the
user rather than writing to the wrong row.

### Step 5: Ask the 3 questions

Ask directly in the conversation (this is a 3-field survey, not worth a
dedicated TUI):

1. **Rating vs. expectations** — offer `MM` / `MA` / `EE` (missed / met
   ambition / exceeded); a single-select question fits well here.
2. **What did I get done last week?** — free text.
3. **What does exceeding expectations this week look like?** — free text.

### Step 6: Write to the `i9` sheet

Via `ix-osa.sh` (never local `osascript` — same OneDrive merge-conflict
risk as every other Neon write):

```applescript
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set s to sheet "i9" of wb
    set value of cell 2 of row ROW_PLACEHOLDER of s to "RATING_PLACEHOLDER"
    set value of cell 3 of row ROW_PLACEHOLDER of s to POINTS_PLACEHOLDER
    set value of cell 4 of row ROW_PLACEHOLDER of s to "GOT_DONE_PLACEHOLDER"
    set value of cell 5 of row ROW_PLACEHOLDER of s to "EXCEEDING_PLACEHOLDER"
    set value of cell 6 of row ROW_PLACEHOLDER of s to MINUTES_PLACEHOLDER
    if (string value of cell 6 of row 2 of s) is "" then
        set value of cell 6 of row 2 of s to "min"
    end if
    return "OK"
end tell
```

Escape quotes/newlines in the free-text answers for AppleScript. `POINTS_PLACEHOLDER`/`MINUTES_PLACEHOLDER` are plain numbers, unquoted.

### Step 7: Complete the `1 i9` habit

Invoke the existing weekly-habit completion path — do **not** hand-write to
the `1n+` sheet:

```
/did 1 i9
```

This closes the recurring `1 i9 (10) [40]` Todoist task (labels
`1neon`,`i9`) and credits the `1n+` sheet's existing column J the normal
way (header-name routing in `did-fast.py`), matching every other `1neon`
habit. `1n+` col J already exists — this skill deliberately does not touch
that sheet's column layout (it's fully packed, C:AL, with no spare column;
see the 2026-09-15 decision not to insert one).

### Step 8: Report

```
i9 week {label} ({week_start}–{week_end})
Minutes: {weekly_minutes}m ({hours}h {mins}m)
Points: {weekly_points}分
Rating: {rating}

Written to i9!row {row} (cols B-F). 1 i9 habit marked done.
```

## Notes

- All Excel writes/reads go through `~/.claude/skills/_lib/ix-osa.sh`. Never
  call local `osascript`.
- The `i9` sheet is lookup-only — it does not auto-append future weeks the
  way some other sheets do misbehave when appended to; if Step 4's
  verification fails, stop rather than inserting a row.
- Minutes (col F) is a new column added by this skill (2026-09-15) — no
  history before this date.
