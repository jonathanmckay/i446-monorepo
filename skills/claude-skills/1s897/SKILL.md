---
name: "1s897"
description: "Weekly social review. Reads Toggl s897+家 entries >20m, writes event list + sum to Neon 0s897 col AA, then compares against priorities and provides improvement analysis."
user-invocable: true
---

# Weekly Social Review (1s897)

Review the most recent complete week's social activities from Toggl, write raw data to the Neon spreadsheet, then provide improvement analysis.

## Response style

Terse. No preamble. Do the work, report results.

## Steps

### Step 1: Calculate the target week (W-Tu)

Weeks run **Wednesday through Tuesday**. Find the most recent **complete** W-Tu week:
- If today is Tuesday, the week ending today is the target (it completes today).
- If today is Wednesday or later, the week ending on the most recent Tuesday is the target.

Calculate `week_start` (Wednesday) and `week_end` (Tuesday).

### Step 2: Fetch Toggl entries

Use the Toggl MCP server (`toggl_date` tool or equivalent) to fetch all time entries for each day from `week_start` through `week_end`.

Filter to only entries in these two projects:
- **s897** (project ID 109719141)
- **家** (project ID 108547409)

Keep only entries with duration **> 20 minutes**.

### Step 3: Format the activity list

Format each qualifying entry as a bullet:

```
• {description}: {duration_minutes}m ({project_code})
```

Sort by day (chronological). At the end, add a total line:

```
Total: {sum_minutes}m ({hours}h {mins}m)
```

### Step 4: Write raw data to Neon 0s897 sheet

Find the correct row in the `0s897` sheet of `Neon分v12.2.xlsx`:
- Scan column B for a date matching `week_end` (the Tuesday).
- That row gets the data.

Write the formatted activity list + total to **column AA** ("Event Review") of that row.
Write the total minutes (as a number) to **column Z** ("Time") of that row.

Use AppleScript to write to Excel:

```bash
osascript -e '
tell application "Microsoft Excel"
  set wb to workbook "Neon分v12.2.xlsx"
  set ws to worksheet "0s897" of wb
  -- Find the row where column B matches the target Tuesday date
  set targetDate to "TARGET_DATE_PLACEHOLDER"
  set lastRow to 200
  repeat with i from 2 to lastRow
    set cellVal to string value of cell ("B" & i) of ws
    if cellVal contains targetDate then
      -- Write event review to AA
      set value of cell ("AA" & i) of ws to "EVENT_REVIEW_PLACEHOLDER"
      -- Write total minutes to Z
      set value of cell ("Z" & i) of ws to TOTAL_MINUTES_PLACEHOLDER
      exit repeat
    end if
  end repeat
end tell'
```

Replace:
- `TARGET_DATE_PLACEHOLDER` with the Tuesday date formatted to match Excel's date display (try M/D/YYYY format, e.g. `4/7/2026`)
- `EVENT_REVIEW_PLACEHOLDER` with the formatted activity list (escape quotes/newlines for AppleScript)
- `TOTAL_MINUTES_PLACEHOLDER` with the numeric total

**Important**: Display the raw data to the user immediately after writing it. The user will be adding their own analysis in parallel.

### Step 5: Read priorities and compare

Read the social priorities from:
- `~/vault/s897/weekly-social-system.md` (weekly scorecard, points budget, quality indicators)
- `~/vault/s897/s897.md` (core philosophy, budgets)

Compare the week's activities against:
1. **Time allocation**: How much time was spent vs. the budget (Social Capital Points: 240/week max, Family = 1pt/min, Self = 0.5pt/min)
2. **Quality indicators**: A/B/C/D week rating based on the criteria (2 in-person things = A week, etc.)
3. **Priorities**: Were the activities aligned with stated goals (consistent quality time, double-booking, prep-driven events)?
4. **Patterns**: Any feast-or-famine signals? Over-indexing on one type?

### Step 6: Write analysis to Neon

Write the improvement analysis to **column AB** ("Notes for Improvement / Upcoming") of the same row.

Format as bullet points matching the existing style:
```
• Key observation about the week
• What went well and should continue
• What to improve next week
• Specific suggestion for next week
```

Use the same AppleScript pattern as Step 4.

### Step 7: Report

Tell the user:
- Week range (W date - Tu date)
- Activities found (the list)
- Total social time
- Social capital points used
- Week grade (A/B/C/D)
- Top improvement suggestion

## Example output

```
W 4/1 - Tu 4/7 | 5 activities | 340m (5h 40m)

• Dinner with Dave + Jenna: 120m (s897)
• Call with Forest: 45m (s897)
• Family board game night: 90m (家)
• Zoo with kids + Angela's family: 55m (家)
• Lunch with Stuart: 30m (s897)
Total: 340m (5h 40m)

Written to 0s897 row 98, cols Z + AA.

Points: 255₹ / 240₹ budget (family 210m = 210₹, self 130m = 65₹)
Grade: A (2+ in-person, daily touchpoints unclear from Toggl)

Analysis written to col AB.
Top suggestion: Double-book more — zoo + family friends was high-ROI.
```

## Dependencies

- Toggl MCP server (toggl_date or toggl_today tools)
- Microsoft Excel with Neon分v12.2.xlsx open
- Files: ~/vault/s897/weekly-social-system.md, ~/vault/s897/s897.md
