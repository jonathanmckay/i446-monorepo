# 0₦ Sheet Specification

> Agent documentation for reading and writing to the `0₦` (zero-neon) sheet in `neon_agent_copy.xlsx`

---

## Overview

The `0₦` sheet is the **detailed daily neon tracking sheet** that serves as a source for many `0分` formulas. Each row represents one day. This sheet contains granular tracking data for sleep, habits, work, health, and spiritual practices.

- **Dimensions:** ~400+ rows × 60+ columns
- **Row 1:** Headers
- **Rows 2-4:** Meta rows (targets, weights, ideal time)
- **Row 5+:** Daily data (one row per day, starting Jan 4, 2026)

---

## Column Reference

### Date Column

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 3 | 2026 | Date | Date as datetime (e.g., `2026-01-21 00:00:00`) | 🔒 Read |

**Note:** Column 3 contains the date. Rows 2-4 contain meta labels (`⊖分`, `p(t)`, `Ideal time`), not dates.

---

### Sleep & Morning (Cols 4-6)

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 4 | 睡觉 | Sleep | Sleep time/quality metric | 🔒 Human |
| 5 | cpap | CPAP | CPAP usage indicator | 🔒 Human |
| 6 | 起 | Wake | Wake time | 🔒 Human |

---

### Time Tracking & Planning (Cols 7-12)

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 7 | i444 | i444 | Time block indicator | 🔒 Human |
| 8 | i47 | i47 | Time block indicator | 🔒 Human |
| 9 | 充 | Charge | Energy/charging time | 🔒 Human |
| 10 | tmrw | Tomorrow | Tomorrow planning done | 🔒 Human |
| 11 | 2hci | 2hr Check-in | 2-hour check-in completed | 🔒 Human |
| 12 | d hci | Daily Check-in | Daily check-in completed | 🔒 Human |

---

### Communication & Media (Cols 13-16)

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 13 | @jm | Journal/Mindfulness | Journaling or mindfulness practice | 🔒 Human |
| 14 | 新闻 | News | News consumption time | 🔒 Human |
| 15 | 词汇 | Vocabulary | Language/vocabulary study | 🔒 Human |
| 16 | hcmc | HCMC | HCMC-related activities | 🔒 Human |

---

### Daily Tracking (Cols 17-22)

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 17 | 0t | Daily Time | Total daily time tracked | 🔒 Read |
| 18 | ₦156 | Neon 156 | Specific neon metric | 🔒 Read |
| 19 | 0l | Daily Log | Daily log completion time | 🔒 Human |
| 20 | 0₲ | Daily Bonus | Daily bonus points | ✅ Write |
| 21 | stats | Statistics | Daily statistics | 🔒 Read |
| 22 | notes | Notes | Daily notes | ✅ Write |

---

### Work & Projects (Cols 23-29)

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 23 | @microsoft, @github | Work Tags | Work-related tags | 🔒 Human |
| 24 | 代 | Code | Coding time (minutes) | ✅ Write |
| 25 | tms | TMS | TMS project time | 🔒 Human |
| 26 | slack | Slack | Slack time (column 1) | 🔒 Human |
| 27 | slack | Slack | Slack time (column 2) | 🔒 Human |
| 28 | @m5 | M5 | M5 project reference | 🔒 Human |
| 29 | dash | Dashboard | Dashboard time | 🔒 Human |

---

### Health & Fitness (Cols 30-31)

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 30 | 早餐 | Breakfast | Breakfast tracking | 🔒 Human |
| 31 | hiit | HIIT | HIIT workout done | 🔒 Human |

---

### Neon Tracking (Cols 32-34)

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 32 | ⎣₦ | Floor Neon | Floor neon metric | 🔒 Read |
| 33 | ⎣∀clr | All Colors | All colors completion flag | 🔒 Read |
| 34 | # | Count | Activity count | 🔒 Read |

---

### Learning & Growth (Cols 35-38)

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 35 | 问学 | Learning | Learning/study time (minutes) | 🔒 Human |
| 36 | qft | QFT | QFT study time | 🔒 Human |
| 37 | lx:qt | LX:QT | LX quantum time | 🔒 Human |
| 38 | NVC + e | NVC + e | NVC and emotional work | 🔒 Human |

---

### Spiritual Practice (Col 39) ⭐

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 39 | ص | Salat/Prayer | **Prayer count (الفاتحة)** — number of prayers recited | ✅ Read |

**This is the primary prayer tracking column.** Values represent the count of الفاتحة (Al-Fatiha) recitations for the day.

Example values:
- `60` — 60 prayers
- `85` — 85 prayers
- `102` — 102 prayers

---

### Additional Tracking (Cols 40-42)

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 40 | o314 | O314 | O314 metric | 🔒 Human |
| 41 | 冥想 | Meditation | Meditation time (minutes) | 🔒 Human |
| 42 | 其他人 | Others | Time with others | 🔒 Human |

---

### Score Modifiers (Cols 44-47)

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 44 | 1+ | Plus One | Positive modifier/bonus | 🔒 Read |
| 45 | -1 | Minus One | Small penalty | 🔒 Read |
| 46 | -2/-4 | Minus 2/4 | Medium penalty | 🔒 Read |
| 47 | -3 | Minus Three | Penalty modifier | 🔒 Read |

---

### Calculated Summaries (Cols 49-53)

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 49 | ∑xk87 | Sum XK87 | Calculated total | 🔒 Read |
| 50 | ∑hcmt | Sum HCMT | HCMT total | 🔒 Read |
| 51 | ∑hcmc | Sum HCMC | HCMC total | 🔒 Read |
| 52 | ∑hcb | Sum HCB | Health/body total | 🔒 Read |
| 53 | 蠢之后 | After Mistakes | Post-mistake tracking | 🔒 Read |

---

### Category Totals (Cols 56-57)

| Col | Header | Name | Description | Agent Access |
|-----|--------|------|-------------|--------------|
| 56 | hcmc | HCMC | HCMC category total | 🔒 Read |
| 57 | m5x2 | M5x2 | Real estate (M5) total | 🔒 Read |

---

## Row Lookup

To find a row by date in the `0₦` sheet:

```python
from openpyxl import load_workbook
import datetime

def find_row_by_date_0N(sheet, target_date):
    """Find row number for a given date in 0₦ sheet.
    
    Note: Date is in Column 3, and data rows start at Row 5.
    Rows 2-4 are meta rows (⊖分, p(t), Ideal time).
    """
    for r in range(5, sheet.max_row + 1):
        cell_val = sheet.cell(row=r, column=3).value  # Col 3 = date
        if isinstance(cell_val, datetime.datetime):
            if cell_val.date() == target_date:
                return r
    return None
```

---

## Prayer Data Access

To get the prayer count for a specific date:

```python
from openpyxl import load_workbook
import datetime

def get_prayer_count(filepath, target_date):
    """Get prayer count (الفاتحة) for a specific date.
    
    Args:
        filepath: Path to xlsx file
        target_date: datetime.date object
        
    Returns:
        int: Number of prayers, or None if not found
    """
    wb = load_workbook(filepath, data_only=True)
    sheet = wb['0₦']
    
    PRAYER_COL = 39  # ص column
    DATE_COL = 3
    
    for r in range(5, sheet.max_row + 1):
        cell_val = sheet.cell(row=r, column=DATE_COL).value
        if isinstance(cell_val, datetime.datetime):
            if cell_val.date() == target_date:
                prayer_val = sheet.cell(row=r, column=PRAYER_COL).value
                wb.close()
                return prayer_val
    
    wb.close()
    return None

# Example usage:
# count = get_prayer_count('neon_agent_copy.xlsx', datetime.date(2026, 1, 10))
# print(f"Prayers: {count}x الفاتحة")
```

---

## Key Differences from 0分

| Aspect | 0分 | 0₦ |
|--------|-----|-----|
| Date Column | Col 2 | Col 3 |
| First Data Row | Row 5 | Row 5 |
| Meta Rows | None | Rows 2-4 |
| Purpose | Daily totals & scoring | Detailed tracking |
| Formula Pattern | Formula + append | Direct values |

---

## Related Sheets

- **`0分`** — Daily totals and point scoring (pulls from 0₦)
- **`0s`** — Daily reflections and notes
- **`hcbi`** — Health/body detailed items

---

*Last updated: February 10, 2026*
