---
name: "0t"
description: "Generate yesterday's Toggl donut chart and update Neon spreadsheet. Automatically gathers time entries, creates visualization, handles Excel insertion, and logs last night's sleep minutes to 0₦."
user-invocable: true
---

# Yesterday's Toggl Donut (0t)

Generate a donut chart visualization of yesterday's Toggl time tracking data and insert it into the Neon spreadsheet. Also computes last night's sleep and logs it to today's row in 0₦.

## What this does

1. **Gather data**: Use the Toggl MCP server to fetch yesterday's time entries
2. **Calculate totals**: Sum minutes per project (excluding 睡觉/sleep)
3. **Generate chart**: Create a color-coded donut chart with:
   - Neon color palette per project
   - Total time in center (HH:MM:SS format)
   - Date label
   - Percentage breakdown
4. **Insert into Excel**: Add the chart to the Neon分v12.2.xlsx spreadsheet (0分 sheet)
5. **Log sleep**: Compute last night's sleep minutes and write to 0₦ sheet column D (睡觉) for today

## Usage

When invoked with `/0t [date]`, follow these steps. If a date is provided (e.g. `3/25`), use it as `yesterday_date`. Otherwise default to actual yesterday.

### Step 1: Get yesterday's Toggl entries

Use `toggl_date` to fetch entries for `yesterday_date` (YYYY-MM-DD format).

### Step 2: Calculate project totals

Sum the duration (in minutes) for each project. Group by project code.

**Important**:
- Convert all time to minutes
- Exclude 睡觉 (sleep) entries from the visualization
- Use project codes: xk87, xk88, m5x2, s897, hcmc, hcb, i9, infra, i444, g245, epcn, n156, 家, etc.

### Step 3: Generate the chart

Run the donut generator script:

```bash
python3 ~/vault/i447/i446/toggl-donut-generator.py \
  --date YYYY-MM-DD \
  --data '{"project1": minutes1, "project2": minutes2, ...}'
```

The script will:
- Create the PNG chart
- Calculate the correct Excel cell location
- Insert the chart using xlwings
- Save a copy to Desktop as `toggl_YYYY-MM-DD.png`

### Step 4: Compute and log last night's sleep

This step runs after the donut is generated. "Last night" = the sleep bridging `yesterday_date` and `today_date` (n+1).

**4a. Get sleep from yesterday's side (pre-midnight):**
- From yesterday's already-fetched entries, filter for project = `睡觉`
- Keep only entries whose start time is **20:00 or later** (avoids naps)
- Sum their durations in minutes

**4b. Get sleep from today's side (post-midnight):**
- Use `toggl_date` to fetch entries for `today_date`
- Filter for project = `睡觉`
- Keep only entries whose start time is **before 14:00** (avoids next night)
- Sum their durations in minutes

**4c. Total sleep = 4a + 4b**

**4d. Write to 0₦ sheet** using osascript:

```applescript
tell application "Microsoft Excel"
    set theSheet to sheet 2 of active workbook  -- 0₦ sheet (use index 2, not name)

    set m to ((month of (current date)) * 1) as text
    set d to ((day of (current date)) * 1) as text
    set today to m & "/" & d

    -- Find today's row in column C (use range references, not cell(r,c))
    set todayRow to 0
    repeat with r from 3 to 500
        set cellRef to "C" & r
        set cellVal to string value of range cellRef of theSheet
        if cellVal = today then
            set todayRow to r
            exit repeat
        end if
    end repeat

    if todayRow = 0 then
        return "ERROR: date " & today & " not found in column C"
    end if

    -- Write sleep minutes to column D (睡觉)
    set targetRef to "D" & todayRow
    set value of range targetRef of theSheet to SLEEP_MINUTES
    return "OK: wrote SLEEP_MINUTES to " & targetRef
end tell
```

Substitute `SLEEP_MINUTES` with the computed total before running.

Run via:
```bash
osascript -e '...'
```

**Note:** The date used for step 4d is always **today** (the actual current date when `/0t` is run), not `yesterday_date`. This is because /0t runs in the morning for yesterday, and last night's sleep is recorded under today.

### Step 5: Confirm completion

Tell the user:
- Chart saved to Desktop
- Chart inserted into Neon spreadsheet (sheet: 0分, cell: AI/AJ{row})
- Total time tracked for the day
- Sleep logged: N min → 0₦ column D (睡觉), today's row

## Example output format

```
✓ Generated Toggl donut chart for 2026-03-30

Total time tracked: 8h 35m
- xk87: 305 min
- m5x2: 155 min
- s897: 113 min
- hcmc: 90 min
- infra: 85 min
- i9: 37 min

Chart saved to:
- Desktop: ~/Desktop/toggl_2026-03-30.png
- Neon spreadsheet: 0分 sheet, cell AI88

Sleep logged: 443 min → 0₦ D (睡觉), today's row
```

## Notes

- The script uses the Neon color palette defined in the Python file
- 睡觉 is excluded from the chart but its duration is used for the sleep log
- Chart dimensions: 6x6 inches at 150 DPI
- Excel insertion uses xlwings (automatic)
- Fallback: If Excel insertion fails, chart is saved to Desktop for manual insertion
- Sleep column: 0₦ sheet, column D, header = 睡觉
- If sleep entries are mislabeled (wrong project), the total will be off — fix in Toggl first

## Dependencies

- Python 3 with matplotlib, xlwings
- Toggl MCP server configured
- Neon分v12.2.xlsx at ~/OneDrive/vault-excel/ and open in Excel
