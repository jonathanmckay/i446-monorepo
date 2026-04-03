---
name: did
description: "Mark habits or tasks as done. Supports multiple items separated by comma/semicolon. Writes to 0₦ (habits) or 0分 (Todoist tasks), completes in Todoist. Usage: /did <habit> [time], <habit2> [time2]"
user-invocable: true
---

# Mark Habit Done (/finished)

Mark a daily habit as complete in the Neon分v12.2.xlsx spreadsheet (`0₦` sheet) using AppleScript, and complete the matching task in Todoist (tagged `0neon`).

## Usage

```
/did <habit> [time]
/did <habit1> [time1], <habit2> [time2], ...
/did <habit1> [time1]; <habit2> [time2]; ...
```

- `<habit>` — the column header as it appears in row 1 of the `0₦` sheet (e.g. `o314`, `冥想`, `hiit`, `0t`), or a Todoist task name
- `[time]` — optional number to write into the cell (minutes). If omitted, search today's Toggl entries for a matching description (see Step 1b). If no Toggl match, writes `1`.
- Multiple items can be separated by `,` or `;` — each is processed independently

Examples:
- `/did o314 20` → writes 20 in the o314 column for today
- `/did hiit` → searches Toggl for "hiit" entries today, sums their minutes, writes that
- `/did 冥想 15` → writes 15 in the 冥想 column for today
- `/did slack m5x2, slack github` → marks both slack habits done (writes 1 each)
- `/did push 4; 早餐; day hci` → marks three habits done in one command

## Steps

### Step -1: Split multiple items

If the input contains `,` or `;`, split into separate items. Trim whitespace from each. Process each item independently through Steps 0–5, then report all results together at the end.

### Step 0: Determine if this is a 0₦ habit or a Todoist task

Try to match `<habit>` against the 0₦ sheet column headers (row 1). If it matches, proceed to Step 1 (normal habit flow).

If it does **not** match any 0₦ column header, treat it as a **Todoist task** instead — jump to [Step 5: Todoist-only task](#step-5-todoist-only-task).

### Step 1: Parse arguments

Extract `<habit>` and optional `[time]` from the arguments.

### Step 1b: Auto-detect time from Toggl (if no time provided)

If the user did not provide `[time]`, use `toggl_today` to fetch today's entries. Search for entries whose description contains `<habit>` (case-insensitive). Sum the duration in minutes across all matching entries. If matches are found, use that sum as `[time]`. If no matches, fall back to `1`.

Use the `/tg` shortcode mapping to expand the habit name for matching. For example:
- `hiit` → also match Toggl project `hcbp`
- `新闻` → also match Toggl project `hcmc`
- `push` → match description containing "push"

Match on **description** first (substring, case-insensitive). If no description match, try matching the habit name against the Toggl **project code**.

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

### Step 2b: Special case — 0l completion time

If the habit is `0l`, check whether `0t` has already been completed today (i.e., the `0t` column in today's row has a non-empty value). If both `0l` and `0t` are done, write the current time as a **4-digit number** (HHMM, 24h format) into column **AF** of today's row in the `0₦` sheet.

For example, if it's 2:34 PM, write `1434`.

Use AppleScript to:
1. Check if the `0t` column for today's row is non-empty
2. If yes, get the current time as HHMM and write it to AF for today's row

```applescript
tell application "Microsoft Excel"
    set theSheet to sheet 2 of active workbook
    set m to ((month of (current date)) * 1) as text
    set d to ((day of (current date)) * 1) as text
    set today to m & "/" & d

    set colLetters to {"A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X","Y","Z","AA","AB","AC","AD","AE","AF","AG","AH","AI","AJ","AK","AL","AM","AN","AP"}

    -- Find today's row
    set todayRow to 0
    repeat with r from 3 to 500
        set cellVal to string value of range ("C" & r) of theSheet
        if cellVal = today then
            set todayRow to r
            exit repeat
        end if
    end repeat

    if todayRow = 0 then return "SKIP: date not found"

    -- Find 0t column
    set otCol to 0
    repeat with c from 1 to count of colLetters
        set colRef to (item c of colLetters) & "1"
        set cellVal to string value of range colRef of theSheet
        if cellVal = "0t" then
            set otCol to c
            exit repeat
        end if
    end repeat

    if otCol = 0 then return "SKIP: 0t column not found"

    -- Check if 0t is done
    set otVal to string value of range ((item otCol of colLetters) & todayRow) of theSheet
    if otVal is not "" and otVal is not "0" then
        -- Write current time as HHMM to column AF
        set h to hours of (current date)
        set mn to minutes of (current date)
        set timeStr to (h * 100 + mn)
        set value of range ("AF" & todayRow) of theSheet to timeStr
        return "OK: wrote " & timeStr & " to AF" & todayRow
    else
        return "SKIP: 0t not done yet"
    end if
end tell
```

### Step 3: Complete matching Todoist task

Search for an active Todoist task that:
- Has the label `0neon`
- Has content matching the habit name (case-insensitive, substring match is fine)

Use the Todoist MCP `find-tasks` tool to search, then `complete-tasks` to mark it done.

If no matching task is found, skip silently (not all habits have a Todoist task).

### Step 4: Report

On success, confirm in one line:
```
<habit> → <time> (today) [+ todoist]
```

If no Todoist task was found, omit the `[+ todoist]` part. On error (habit not found, date not found), report the error clearly.

### Step 5: Todoist-only task

This step runs when `<habit>` is **not** a 0₦ column header.

1. **Find the task in Todoist.** Search active tasks for today (due today or overdue) whose content matches `<habit>` (case-insensitive, substring). If no match, report "task not found" and stop.

2. **Extract points from the task name.** Todoist tasks often have `[N]` in the name (e.g. `check in on f694 (10) [10]`). Parse the number inside `[...]` as the points value. If no `[N]` found, ask the user how many points.

3. **Determine the 0分 column.** Use the task's Todoist labels/tags to map to a 0分 column:
   - Labels containing a known domain code → use the 1nd mapping table (from the `/1nd` skill)
   - Common mappings: `i9`/`i447`/`f693`/`f694` → AA (i9), `m5x2`/`m5` → AB (m5), `g245` → AC (个), `hcmc` → AD (媒), `xk87`/`xk88`/`xk` → AG (xk), `s897`/`社` → AH (社), `hcb`/`hcbp` → AF (hcb)
   - If ambiguous or no label matches, ask the user which 0分 column.

4. **Append points to today's 0分 row.** Use AppleScript to find today's row in 0分 (date column B, M/D format), then **append** `+N` to the existing formula in the target column. Never overwrite.

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
        set formula of theCell to oldFormula & "+POINTS"
        return "OK: appended +POINTS to COL" & todayRow
    end if
end tell
```

5. **Close the Todoist task.**

6. **Report:**
```
✓ <task> → +<points> to <col> (<label>) [todoist closed]
```

## Notes

- The `0₦` sheet **must be open** in Excel for AppleScript to work. If Excel isn't running or the file isn't open, tell the user to open `~/OneDrive/vault-excel/Neon分v12.2.xlsx`.
- Column headers are in **row 1**. The habit name must match exactly (case-sensitive).
- Date is in column C in **M/D format** (e.g. `3/30`, not `03/30`).
- If the user passes a habit shortcode that looks different from the column header, they need to use the exact header string from row 1.
