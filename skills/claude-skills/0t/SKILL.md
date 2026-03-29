---
name: 0t
description: Generate yesterday's Toggl donut chart and update Neon spreadsheet. Automatically gathers time entries, creates visualization, and handles Excel insertion.
user-invocable: true
---

# Yesterday's Toggl Donut (0t)

Generate a donut chart visualization of yesterday's Toggl time tracking data and insert it into the Neon spreadsheet.

## What this does

1. **Gather data**: Use the Toggl MCP server to fetch yesterday's time entries
2. **Calculate totals**: Sum minutes per project (excluding 睡觉/sleep)
3. **Generate chart**: Create a color-coded donut chart with:
   - Neon color palette per project
   - Total time in center (HH:MM:SS format)
   - Date label
   - Percentage breakdown
4. **Insert into Excel**: Add the chart to the Neon分v12.2.xlsx spreadsheet (0分 sheet)

## Usage

When invoked with `/0t`, follow these steps:

### Step 1: Get yesterday's Toggl entries

Use the toggl_server MCP to fetch yesterday's time entries. The server is at `~/i446-monorepo/toggl_server/`.

Calculate yesterday's date and fetch entries for that date.

```python
# Calculate yesterday's date (today - 1 day)
# Use toggl API to get all entries for yesterday
```

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

### Step 4: Confirm completion

Tell the user:
- Chart saved to Desktop
- Chart inserted into Neon spreadsheet (sheet: 0分, cell: AI/AJ{row})
- Total time tracked for the day

## Example output format

```
✓ Generated Toggl donut chart for 2026-03-24

Total time tracked: 8h 35m
- xk87: 305 min
- m5x2: 155 min
- s897: 113 min
- hcmc: 90 min
- infra: 85 min
- i9: 37 min

Chart saved to:
- Desktop: ~/Desktop/toggl_2026-03-24.png
- Neon spreadsheet: 0分 sheet, cell AI83
```

## Notes

- The script uses the Neon color palette defined in the Python file
- 睡觉 is excluded from the chart but should still be tracked in Toggl
- Chart dimensions: 6x6 inches at 150 DPI
- Excel insertion uses xlwings (automatic)
- Fallback: If Excel insertion fails, chart is saved to Desktop for manual insertion

## Dependencies

- Python 3 with matplotlib, xlwings
- Toggl MCP server configured
- Neon分v12.2.xlsx at ~/OneDrive/vault-excel/
