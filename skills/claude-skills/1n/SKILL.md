---
name: 1n
description: Generate weekly Toggl donut charts (two versions) and update Neon spreadsheet. Automatically gathers time entries for the most recent week (Sunday-Saturday), creates two visualizations with different exclusions, and handles Excel insertion.
user-invocable: true
---

# Weekly Toggl Donuts (1n)

Generate two donut chart visualizations of the most recent week's Toggl time tracking data and insert them into the Neon spreadsheet.

## What this does

1. **Gather data**: Use the Toggl MCP server to fetch the most recent week's time entries (Sunday-Saturday)
2. **Calculate totals**: Sum minutes per project with two different exclusion sets
3. **Generate charts**: Create two color-coded donut charts:
   - **Chart 1**: All projects except 睡觉
   - **Chart 2**: Excludes 睡觉, i9, xk87, xk88
4. **Insert into Excel**: Add both charts to the Neon分v12.2.xlsx spreadsheet (1分+1s sheet)

## Usage

When invoked with `/1n`, follow these steps:

### Step 1: Calculate week dates

Determine the most recent complete week (Sunday-Saturday). If today is Sunday, use the previous week. Otherwise, find the most recent Saturday and go back to that week's Sunday.

```python
# Calculate the most recent Sunday-Saturday week
# If today is Sunday, use last week (7 days ago to yesterday)
# Otherwise, find the most recent Saturday and go back to its Sunday
```

### Step 2: Get week's Toggl entries

Use the toggl_server MCP to fetch all time entries for the calculated week.

The toggl_server supports fetching entries for a date range. Use the appropriate tool to get all entries from Sunday through Saturday.

### Step 3: Calculate project totals

Sum the duration (in minutes) for each project. Group by project code.

**Important**:
- Convert all time to minutes
- Create TWO datasets:
  1. All projects except 睡觉
  2. Excludes 睡觉, i9, xk87, xk88
- Use project codes: xk87, xk88, m5x2, s897, hcmc, hcb, i9, infra, i444, g245, epcn, n156, 家, etc.

### Step 4: Generate both charts

You'll need to run the donut generator script twice, once for each dataset.

**Chart 1** (excludes only 睡觉):
```bash
python3 ~/vault/i447/i446/toggl-donut-generator.py \
  --date YYYY-MM-DD \
  --data '{"project1": minutes1, "project2": minutes2, ...}'
```

**Chart 2** (excludes 睡觉, i9, xk87, xk88):
```bash
python3 ~/vault/i447/i446/toggl-donut-generator.py \
  --date YYYY-MM-DD \
  --data '{"project1": minutes1, "project2": minutes2, ...}'
```

### Step 5: Insert into Neon spreadsheet

The charts should be inserted into:
- **Sheet**: 1分+1s
- **Cells**: O{week_number} and P{week_number}
  - For week 12 of 2026: O12 and P12
  - For week 13 of 2026: O13 and P13
  - etc.

Calculate the ISO week number for the week being visualized to determine the correct row.

The script will:
- Create the PNG charts
- Insert chart 1 into column O (excludes 睡觉 only)
- Insert chart 2 into column P (excludes 睡觉, i9, xk87, xk88)
- Save copies to Desktop as `toggl_week{N}_all.png` and `toggl_week{N}_focus.png`

### Step 6: Confirm completion

Tell the user:
- Charts saved to Desktop
- Charts inserted into Neon spreadsheet (sheet: 1分+1s, cells: O{N}, P{N})
- Total time tracked for the week
- Week date range (Sunday-Saturday)
- Breakdown for both chart versions

## Example output format

```
✓ Generated Toggl donut charts for Week 12 (2026-03-16 to 2026-03-22)

Chart 1 (excludes 睡觉 only):
Total time tracked: 58h 35m
- xk87: 1305 min
- m5x2: 855 min
- s897: 613 min
- hcmc: 590 min
- i9: 485 min
- infra: 285 min

Chart 2 (excludes 睡觉, i9, xk87, xk88):
Total time tracked: 35h 12m
- m5x2: 855 min
- s897: 613 min
- hcmc: 590 min
- infra: 285 min

Charts saved to:
- Desktop: ~/Desktop/toggl_week12_all.png, ~/Desktop/toggl_week12_focus.png
- Neon spreadsheet: 1分+1s sheet, cells O12 and P12
```

## Notes

- The script uses the Neon color palette defined in the Python file
- 睡觉 is excluded from both charts but should still be tracked in Toggl
- Chart dimensions: 6x6 inches at 150 DPI
- Excel insertion uses xlwings (automatic)
- Fallback: If Excel insertion fails, charts are saved to Desktop for manual insertion
- Week calculation uses ISO week numbering (week starting on Monday), but data collection is Sunday-Saturday

## Dependencies

- Python 3 with matplotlib, xlwings
- Toggl MCP server configured
- Neon分v12.2.xlsx at ~/OneDrive/vault-excel/
