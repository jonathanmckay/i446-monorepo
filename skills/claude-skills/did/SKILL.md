---
name: did
description: Mark a daily habit as done in the Neon 0₦ sheet via AppleScript. Usage: /did <habit> [time]
user-invocable: true
---

# Mark Habit Done (/finished)

Mark a daily habit as complete in the Neon分v12.2.xlsx spreadsheet (`0₦` sheet) using AppleScript.

## Usage

```
/finished <habit> [time]
```

- `<habit>` — the column header as it appears in row 1 of the `0₦` sheet (e.g. `o314`, `冥想`, `hiit`, `0t`)
- `[time]` — optional number to write into the cell (minutes). If omitted, writes `1`.

Examples:
- `/finished o314 20` → writes 20 in the o314 column for today
- `/finished hiit` → writes 1 in the hiit column for today
- `/finished 冥想 15` → writes 15 in the 冥想 column for today

## Steps

### Step 1: Parse arguments

Extract `<habit>` and optional `[time]` from the arguments. If no time provided, use `1`.

### Step 2: Run AppleScript

Run the following AppleScript via `osascript`. It:
1. Opens the `0₦` sheet
2. Scans row 1 for the column matching `<habit>` (exact match)
3. Scans column C for today's date in M/D format
4. Writes `[time]` into the matching cell

```applescript
tell application "Microsoft Excel"
    set theSheet to sheet 2 of active workbook  -- 0₦ sheet (use index, not name, to avoid encoding issues)

    set m to ((month of (current date)) * 1) as text
    set d to ((day of (current date)) * 1) as text
    set today to m & "/" & d

    set colLetters to {"A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X","Y","Z","AA","AB","AC","AD","AE","AF","AG","AH","AI","AJ","AK","AL","AM","AN","AP"}

    set habitName to "HABIT_PLACEHOLDER"
    set habitCol to 0
    repeat with c from 1 to count of colLetters
        set colRef to (item c of colLetters) & "1"
        set cellVal to string value of range colRef of theSheet
        if cellVal = habitName then
            set habitCol to c
            exit repeat
        end if
    end repeat

    if habitCol = 0 then
        return "ERROR: habit '" & habitName & "' not found in row 1"
    end if

    set todayRow to 0
    repeat with r from 3 to 500
        set cellVal to string value of range ("C" & r) of theSheet
        if cellVal = today then
            set todayRow to r
            exit repeat
        end if
    end repeat

    if todayRow = 0 then
        return "ERROR: date " & today & " not found in column C"
    end if

    set targetRef to (item habitCol of colLetters) & todayRow
    set value of range targetRef of theSheet to TIME_PLACEHOLDER
    return "OK: wrote TIME_PLACEHOLDER to " & targetRef
end tell
```

Before running, substitute:
- `HABIT_PLACEHOLDER` → the habit name from the user's input
- `TIME_PLACEHOLDER` → the time value (number)

Run via:
```bash
osascript -e '...'
```

### Step 3: Report

On success, confirm in one line:
```
✓ <habit> → <time> (today)
```

On error (habit not found, date not found), report the error clearly.

## Notes

- The `0₦` sheet **must be open** in Excel for AppleScript to work. If Excel isn't running or the file isn't open, tell the user to open `~/OneDrive/vault-excel/Neon分v12.2.xlsx`.
- Column headers are in **row 1**. The habit name must match exactly (case-sensitive).
- Date is in column C in **M/D format** (e.g. `3/30`, not `03/30`).
- If the user passes a habit shortcode that looks different from the column header, they need to use the exact header string from row 1.
