---
title: Toggl Donut Chart Generator - Usage Guide
date: 2026-03-20
type: doc
tags: [i447, i446, toggl, automation]
---

# Toggl Donut Chart Generator

Script location: `~/vault/i447/i446/toggl-donut-generator.py`

## Quick Usage (from Claude)

Just tell Claude:
```
"Create donut chart for [date] with toggl data"
```

Claude will:
1. Gather the Toggl time entries for that date
2. Calculate minutes per project (excluding 睡觉)
3. Run the script to generate the chart
4. Give you instructions for inserting it

## Manual Usage

```bash
python3 ~/vault/i447/i446/toggl-donut-generator.py \
  --date 2026-03-19 \
  --data '{"xk87": 305, "m5x2": 155, "s897": 113, "hcmc": 90, "infra": 85, "i9": 37, "hcb": 30}'
```

### Parameters

- `--date`: Date in YYYY-MM-DD format
- `--data`: JSON dictionary of `{project: minutes}`
  - Excludes 睡觉 (sleep) automatically
  - Use project codes: xk87, m5x2, s897, hcmc, infra, i9, hcb, etc.
- `--output`: (Optional) Custom output path for PNG
- `--no-insert`: (Optional) Skip Excel insertion step

### Color Mapping

Colors come from `~/vault/i447/neon-color-pallette.md`:

- xk87: #fd6c1d (Tangerine Dream)
- xk88: #e65100 (Molten)
- m5x2: #d50032 (Crimson)
- s897: #1b5e20 (Emerald Shadow)
- hcmc: #0d3b66 (Deep Sea)
- hcb: #f81d78 (Bubblegum Shock)
- i9: #2979ff (Electric Blue)
- infra: #9e9e9e (Concrete)
- no project: #616161 (Graphite)

## Output

1. Creates PNG chart with:
   - Neon color palette
   - No legend
   - Date label (instead of "PROJECT")
   - Total time in center (HH:MM:SS)

2. Saves to Desktop as `toggl_YYYY-MM-DD.png`

3. Provides instructions for inserting at the correct cell in Neon分v12.xlsx (0分 sheet, column AI, calculated row based on date)

## Excel Insertion

The script calculates the correct row automatically:
- Row 3 = 2026-01-04
- Each day after adds 1 row
- Example: 2026-03-19 = row 77

Manual steps (most reliable):
1. Open Neon分v12.xlsx
2. Go to 0分 sheet
3. Cmd+G → AI{row number}
4. Insert → Pictures → Picture from File
5. Select the chart from Desktop
6. Resize to ~200x200px to match other charts

## Why Manual Insertion?

Python's openpyxl library destroys existing Excel images when saving. AppleScript has unreliable access to Excel's object model. Manual insertion is fastest and safest.
