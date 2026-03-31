---
name: 1nd
description: Log completion of a 1₦+ weekly task and add its points to today's 0分 tab.
user-invocable: true
---

# Log 1₦+ Task Done (/1nd)

Mark a weekly 1₦+ task as complete by adding its points to the appropriate column in today's row in the 0分 sheet.

## Usage

```
/1nd <task> [points]
```

- `<task>` — the task name as it appears in row 1 of the 1₦+ sheet (case-insensitive partial match OK)
- `[points]` — optional. If omitted, link to the e(分) cell in 1₦+ (row 3) rather than hardcoding the number

## Steps

### Step 1: Parse input

Extract `<task>` and optional `[points]`.

### Step 2: Look up task in 1₦+ sheet

Scan row 1 of the `1₦+` sheet (starting at column B) to find the column matching `<task>` (partial, case-insensitive OK). Note the **column letter** — you'll need it to build a cell reference.

- Row 1 = task names (starts at column B)
- Row 2 = p(t) values
- Row 3 = e(分) default points
- Rows 4+ = weekly data rows (column A = period code like "sf21", column B = week index like "3.5")
- **Current week row** = find the row where column B matches `M.W` where M = current month number and W = current week-of-month (1–5). Week-of-month: week 1 = days 1–7, week 2 = 8–14, week 3 = 15–21, week 4 = 22–28, week 5 = 29–31. All rows have col A pre-populated, so do NOT use "last non-empty col A" — that will land on the wrong row. Instead match col B numerically. E.g. March 30 → month=3, week=5 → B=3.5 → row 16.

```applescript
tell application "Microsoft Excel"
    set ws to sheet "1₦+" of active workbook
    set colLetters to {"B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X","Y","Z","AA","AB","AC","AD","AE","AF","AG","AH","AI","AJ"}
    set foundColLetter to ""
    repeat with c from 1 to count of colLetters
        set colRef to (item c of colLetters) & "1"
        set cv to string value of range colRef of ws
        if cv contains "TASK" then  -- partial match
            set foundColLetter to (item c of colLetters)
            exit repeat
        end if
    end repeat
end tell
```

### Step 3: Map task to 0分 column

Use this table to determine which column in the 0分 sheet to add points to:

| 1₦+ task (row 1)  | 0分 column | Label | Confidence |
|--------------------|------------|-------|------------|
| `5^1 s`            | AC         | 个    | confirmed  |
| `5^1 g`            | AC         | 个    | high       |
| `i9 HPM`           | AA         | i9    | high       |
| `社+hcbp`          | AH         | 社    | medium     |
| `时→f692`          | Z          | 0₲    | medium     |
| `f693`             | AA         | i9    | medium     |
| `i9/m7`            | AA         | i9    | medium     |
| `1 -2₲`            | AC         | 个    | confirmed  |
| `VM\|LI\|MSGR`     | AA         | i9    | confirmed  |
| `-1₦ checkin`      | Y          | -1₦   | confirmed  |
| `f694`             | AB         | m5    | low        |
| `1 xk88`           | AH         | 社    | medium     |
| `1 xk87`           | AG         | xk    | medium     |
| `周末 + aos`        | AG         | xk    | medium     |
| `5^1*2 c084`       | AC         | 个    | medium     |
| `一起饭`            | AG         | xk    | confirmed  |
| `s897`             | AH         | 社    | high       |
| `5^1 hcmc`         | AD         | 媒    | high       |

For any task marked "low" confidence, confirm with the user before writing. Update this table as mappings are confirmed.

### Step 4: Add points to today's 0分 row

Find today's row in 0分 (date column B, M/D format), then **append** to the existing formula — do not replace the value.

```applescript
tell application "Microsoft Excel"
    set theSheet to sheet "0分" of active workbook
    set m to ((month of (current date)) * 1) as text
    set d to ((day of (current date)) * 1) as text
    set today to m & "/" & d
    set todayRow to 0
    repeat with i from 2 to 200
        if (string value of cell ("B" & i) of theSheet) = today then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow > 0 then
        set theCell to range ("COL" & todayRow) of theSheet
        set oldFormula to formula of theCell
        -- Use cell reference if no override, otherwise hardcode
        set formula of theCell to oldFormula & "+POINTS_EXPR"
    end if
end tell
```

- If **no points override**: `POINTS_EXPR` = `'1₦+'!{colLetter}{weekRow}` where `weekRow` is the current week's row (found by scanning column A downward from row 4 for the last non-empty row)
- If **points override provided**: `POINTS_EXPR` = the literal number (e.g. `+30`)

### Step 5: Confirm

One line:
```
✓ <task> → +<points> to <col> (<label>)
```

## Notes

- The 0分 sheet date column is **B** (M/D format, e.g. `3/30`)
- Always **append** to existing formula (`oldFormula & "+N"`), never overwrite
- If the task maps to two columns (e.g. `社+hcbp` could be AH and AF), default to the primary one (AH) unless the user specifies otherwise
