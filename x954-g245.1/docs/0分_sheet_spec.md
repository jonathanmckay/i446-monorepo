# 0分 Sheet Specification

> Agent documentation for reading and writing to the `0分` (zero-fen) sheet in `neon_agent_copy.xlsx`

---

## Overview

The `0分` sheet is a **daily points/activity tracking sheet** spanning one calendar year. Each row represents one day. Rows are pre-populated for the entire year, including future dates—**agents should never add new rows**.

- **Dimensions:** ~435 rows × 45 columns
- **Row 1:** Headers
- **Row 5+:** Daily data (one row per day)

---

## Column Reference

### Identification Columns (Read-Only)

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 1 | 周 | Week.Day | Week number + day (e.g., `1.1` = Week 1, Day 1) | 🔒 Read |
| 2 | 5^0 | Date | Date as datetime (e.g., `2026-01-21 00:00:00`) | 🔒 Read |
| 3 | 地 | Location | Location code (`sf21`, `SEA`, etc.) — reference only, no calculation impact | 🔒 Read |

### Calculated Totals (Read-Only)

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 4 | Σ | Sum | Daily total score (calculated) | 🔒 Read |
| 5 | 0₦t | Neon Time | Neon time total (calculated) | 🔒 Read |
| 6 | ∀₦t | All Neon Time | All neon time total (calculated) | 🔒 Read |

### Time Blocks — 2-Hour Segments (Human/Excel Only)

Fixed 2-hour time periods. **Odd columns** = habit completion flag (1 = done). **Even columns** = points earned in that block.

| Cols | Header | English | Hours (24h) | Agent Access |
|------|--------|---------|-------------|--------------|
| 7-8 | فجر | Fajr/Dawn | 04:00–06:00 | 🔒 Human |
| 9-10 | شروق | Sunrise | 06:00–08:00 | 🔒 Human |
| 11-12 | صباح | Morning | 08:00–10:00 | 🔒 Human |
| 13-14 | ظهر | Noon | 10:00–12:00 | 🔒 Human |
| 15-16 | عصر | Afternoon | 12:00–14:00 | 🔒 Human |
| 17-18 | آصيل | Late Afternoon | 14:00–16:00 | 🔒 Human |
| 19-20 | غروب | Sunset | 16:00–18:00 | 🔒 Human |
| 21-22 | غسق | Dusk | 18:00–20:00 | 🔒 Human |
| 23-24 | صدفة | Night | 20:00–22:00 | 🔒 Human |

- **Odd columns (7, 9, 11...):** `-1 habits` flag — user enters `1` when habit completed
- **Even columns (8, 10, 12...):** Points accumulated in that 2-hour block

### Time Scale Notation

Numbers with ₦ or ₲ indicate time scale:

| Prefix | Time Scale | Example |
|--------|------------|----------|
| -1 | 2 hours | -1₦ = 2-hour habits |
| 0 | 1 day | 0₦ = daily neon |
| 1 | 1 week | 1₦ = weekly neon |

### Habit & Bonus Columns

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 25 | -1₦ | 2-Hour Habits | Habits tracked per 2-hour block (calculated) | 🔒 Read |
| 26 | 0₲ | Daily Bonus | Daily goal bonus points — **formula + append** | ✅ Write (append) |



### Category Columns — Formula + Append Pattern

These columns use formulas that reference other sheets. **Agent appends values** to the existing formula (e.g., `+ 30` or `- 10`), never overwrites.

**IMPORTANT: Top-Level vs Sub-Level Points**

There are two ways to add points, depending on the category:

1. **Top-level categories** (append to formula in `0分`): These are the main point categories that roll up into the daily total. Points added here appear directly in the Σ sum. Use this for bonuses, completions, or activities not tracked in sub-sheets.

2. **Sub-level categories** (edit cell in `0₦` or other source sheet): These feed into the `0分` formulas automatically. Use this when the activity is already tracked in the source sheet's structure.

**When in doubt, use top-level (append to `0分`)**. This is safer and ensures points are counted.

| Col | Header | Name | Alias | Description | Source Sheet | Agent Access |
|-----|--------|------|-------|-------------|--------------|--------------|
| 27 | i9 | Work | `代`, `代码` | Work-related points (coding, etc.) | `0₦` col BI | ✅ Append |
| 28 | m7 | Real Estate | `m5x2` | Real estate business points | `0₦` col BE | ✅ Append |
| 29 | 个 | Personal | — | Everything else | `0₦` col BC | ✅ Append |
| 30 | 媒 | Media | — | Media consumed (see Notion HCMC doc) | `0₦` col BD | ✅ Append |
| 31 | 思 | Mental | — | Mental health / HCMC | `0₦` col AX | ✅ Append |
| 32 | hcb | Health/Body | — | Physical health, nutrition, fitness | `hcbi` sheet | ✅ Append |
| 33 | 家 | Family | — | Extended family activities | `0₦` col AW | ✅ Append |
| 34 | 社 | Social | — | Friends / social activities | Manual or `=0` | ✅ Append |

**Common Aliases:**
- `代` or `代码` → i9 (Col 27, work/coding)
- `m5x2` → m7 (Col 28, real estate)


### Formula Append Pattern

For columns 26–34, the cell contains a formula like:

```
='0₦'!BI25 + 30 + 10 - 20
```

**To add points**, append to the formula:

```python
# Example: Add 45 points to i9 (col 27) for row 22
current = "='0₦'!BI25 + 30"
new_value = current + " + 45"
# Result: "='0₦'!BI25 + 30 + 45"
```

**To subtract points**, append with minus:

```python
current = "='0₦'!BI25 + 30"
new_value = current + " - 15"
# Result: "='0₦'!BI25 + 30 - 15"
```

### Rules

1. **Never overwrite** the base formula reference (e.g., `='0₦'!BI25`)
2. **Always append** with ` + X` or ` - X`
3. **Use integers** for point values
4. **Preserve existing appended values** — add to the end

---

## Row Lookup

To find a row by date:

```python
from openpyxl import load_workbook
import datetime

def find_row_by_date(sheet, target_date):
    """Find row number for a given date."""
    for r in range(2, sheet.max_row + 1):
        cell_val = sheet.cell(row=r, column=2).value  # Col 2 = date
        if isinstance(cell_val, datetime.datetime):
            if cell_val.date() == target_date:
                return r
    return None
```

---

## Completeness Levels

### Level 1: Morning Habits
- **n156** (Col 35): Time tracking done for previous day
- **0l/分**: Recorded the time this was completed
- Indicates basic daily tracking is complete

### Level 2: All Colors
- All `-1 habits` completed (odd columns 7–23 have `1`)
- Results in **blue formatted cells** in Excel
- Indicates full daily habit completion

---

## Example: Agent Appending Points

```python
from openpyxl import load_workbook

def append_points(filepath, date, column, points):
    """
    Append points to a formula cell.
    
    Args:
        filepath: Path to xlsx file
        date: datetime.date object
        column: Column number (26-34)
        points: Integer (positive or negative)
    """
    wb = load_workbook(filepath)
    sheet = wb['0分']
    
    # Find row
    row = find_row_by_date(sheet, date)
    if not row:
        raise ValueError(f"Date {date} not found")
    
    # Get current formula
    cell = sheet.cell(row=row, column=column)
    current = cell.value or ""
    
    # Append points
    if points >= 0:
        cell.value = f"{current} + {points}"
    else:
        cell.value = f"{current} - {abs(points)}"
    
    wb.save(filepath)
```

---

## Column Quick Reference

| Col | Header | Writable | Pattern |
|-----|--------|----------|---------|
| 1-6 | Meta | ❌ | — |
| 7-24 | Time Blocks | ❌ | Human only |
| 25 | -1₦ | ❌ | Calculated |
| 26 | 0₲ | ✅ | Formula + append |
| 27 | i9 | ✅ | Formula + append |
| 28 | m7 | ✅ | Formula + append |
| 29 | 个 | ✅ | Formula + append |
| 30 | 媒 | ✅ | Formula + append |
| 31 | 思 | ✅ | Formula + append |
| 32 | hcb | ✅ | Formula + append |
| 33 | 家 | ✅ | Formula + append |
| 34 | 社 | ✅ | Formula + append |
| 35+ | Tracking | ❌ | Read only |

---

## Related Sheets

- **`0₦`** — Detailed neon tracking (source for many formulas)
- **`hcbi`** — Health/body items (source for hcb column)
- **`0s`** — Daily reflections and notes

---

*Last updated: February 7, 2026*
