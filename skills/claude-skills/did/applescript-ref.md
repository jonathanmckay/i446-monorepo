# /did AppleScript Reference

Templates for Excel operations. Read this file only when executing /did steps in the background agent.

## Substitution variables (all templates)

- `TARGET_MONTH` → month integer from targetDate (e.g. `4`)
- `TARGET_DAY` → day integer from targetDate (e.g. `7`)
- `HABIT_PLACEHOLDER` → habit name
- `TIME_PLACEHOLDER` → time value (number)
- `TARGET_DATE` → M/D format date string

## Template: Write to 0₦ (Step 2)

```applescript
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set theSheet to sheet "0n" of wb
    set targetMonth to TARGET_MONTH
    set targetDay to TARGET_DAY
    set habitName to "HABIT_PLACEHOLDER"
    set habitCol to 0
    repeat with c from 1 to 60
        set cellVal to value of cell c of row 1 of theSheet
        if cellVal is not missing value then
            set trimmed to do shell script "printf '%s' " & quoted form of (cellVal as text) & " | sed 's/[[:space:]]*$//'"
            if trimmed = habitName then
                set habitCol to c
                exit repeat
            end if
        end if
    end repeat
    if habitCol = 0 then
        return "ERROR: habit '" & habitName & "' not found in row 1"
    end if
    set todayRow to 0
    repeat with r from 3 to 500
        set cellDate to value of cell 3 of row r of theSheet
        if cellDate is not missing value then
            try
                set m to (month of (cellDate as date)) as integer
                set d to day of (cellDate as date)
                if m = targetMonth and d = targetDay then
                    set todayRow to r
                    exit repeat
                end if
            end try
        end if
    end repeat
    if todayRow = 0 then
        return "ERROR: date " & targetMonth & "/" & targetDay & " not found in column C"
    end if
    set value of cell habitCol of row todayRow of theSheet to TIME_PLACEHOLDER
    set writtenVal to value of cell habitCol of row todayRow of theSheet
    return "OK: wrote TIME_PLACEHOLDER to col " & habitCol & " row " & todayRow & " (verify=" & (writtenVal as text) & ")"
end tell
```

### Cumulative variant (for 问学 column)

Replace the `set value` line with:
```applescript
set oldVal to value of cell habitCol of row todayRow of theSheet
if oldVal is missing value or (oldVal as text) = "" or (oldVal as text) = "0" then
    set newVal to TIME_PLACEHOLDER
else
    set newVal to (oldVal as number) + TIME_PLACEHOLDER
end if
set value of cell habitCol of row todayRow of theSheet to newVal
```

## Template: 0l completion time (Step 2b)

Only runs when habit is `0l`. Checks if 0t is done, writes HHMM to the 0l⌚ column.

```applescript
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set theSheet to sheet "0n" of wb
    set targetMonth to TARGET_MONTH
    set targetDay to TARGET_DAY
    set todayRow to 0
    repeat with r from 3 to 500
        set cellDate to value of cell 3 of row r of theSheet
        if cellDate is not missing value then
            try
                set m to (month of (cellDate as date)) as integer
                set d to day of (cellDate as date)
                if m = targetMonth and d = targetDay then
                    set todayRow to r
                    exit repeat
                end if
            end try
        end if
    end repeat
    if todayRow = 0 then return "SKIP: date not found"
    set otCol to 0
    repeat with c from 1 to 60
        set cellVal to value of cell c of row 1 of theSheet
        if cellVal is not missing value then
            set trimmed to do shell script "printf '%s' " & quoted form of (cellVal as text) & " | sed 's/[[:space:]]*$//'"
            if trimmed = "0t" then
                set otCol to c
                exit repeat
            end if
        end if
    end repeat
    if otCol = 0 then return "SKIP: 0t column not found"
    set otVal to value of cell otCol of row todayRow of theSheet
    if otVal is not missing value and (otVal as text) is not "0" and (otVal as text) is not "" then
        set afCol to 0
        repeat with c from 1 to 60
            set cellVal to value of cell c of row 1 of theSheet
            if cellVal is not missing value then
                set trimmed to do shell script "printf '%s' " & quoted form of (cellVal as text) & " | sed 's/[[:space:]]*$//'"
                if trimmed = "0l⌚" or trimmed = "AF" then
                    set afCol to c
                    exit repeat
                end if
            end if
        end repeat
        if afCol = 0 then set afCol to 32
        set h to hours of (current date)
        set mn to minutes of (current date)
        set timeStr to (h * 100 + mn)
        set value of cell afCol of row todayRow of theSheet to timeStr
        return "OK: wrote " & timeStr & " to col " & afCol & " row " & todayRow
    else
        return "SKIP: 0t not done yet"
    end if
end tell
```

## Template: Append to 0分 (Step 5)

```applescript
tell application "Microsoft Excel"
    set theSheet to sheet "0分" of active workbook
    set today to "TARGET_DATE"
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
        delay 0.1
        set newFormula to formula of theCell
        set newVal to string value of theCell
        if newFormula does not contain "+POINTS" then
            set formula of theCell to oldFormula & "+POINTS"
            delay 0.3
            set newFormula to formula of theCell
            set newVal to string value of theCell
        end if
        return "OK: appended +POINTS to COL" & todayRow & " (verify=" & newVal & ", formula=" & newFormula & ")"
    else
        return "ERROR: date TARGET_DATE not found in 0分 col B"
    end if
end tell
```

Substitute `COL` with the target column letter (e.g. `AA`, `AB`) and `POINTS` with the number.

## Template: 1n+ write (Step 1n)

```applescript
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set sheet1n to sheet "1n+" of wb
    set colLetters to {"A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X","Y","Z","AA","AB","AC","AD","AE","AF","AG","AH","AI","AJ","AK","AL","AM","AN","AP"}
    set habitName to "HABIT_PLACEHOLDER"
    set habitCol to 0
    set habitColLetter to ""
    repeat with c from 3 to count of colLetters
        set colRef to (item c of colLetters) & "1"
        set cellVal to do shell script "printf '%s' " & quoted form of (string value of range colRef of sheet1n) & " | sed 's/[[:space:]]*$//'"
        if cellVal = habitName then
            set habitCol to c
            set habitColLetter to item c of colLetters
            exit repeat
        end if
    end repeat
    if habitCol = 0 then return "ERROR: habit not found in 1n+ row 1"
    set pointsVal to string value of range (habitColLetter & "3") of sheet1n
    set weekRow to 0
    set targetMW to "MW_PLACEHOLDER"
    repeat with r from 4 to 100
        set bVal to string value of range ("B" & r) of sheet1n
        if bVal = targetMW then
            set weekRow to r
            exit repeat
        end if
    end repeat
    if weekRow = 0 then return "ERROR: week row not found for " & targetMW
    set value of range (habitColLetter & weekRow) of sheet1n to (pointsVal as number)
    return "OK: " & habitColLetter & " pts=" & pointsVal & " weekRow=" & weekRow
end tell
```

MW_PLACEHOLDER = month (no leading zero) + "." + ceil(day/7) (e.g. `4.2` for April 8-14).

## Template: 1n+ → 0分 cell reference (Step 1n, part 2)

```applescript
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set sheet0fen to sheet "0分" of wb
    set today to "TARGET_DATE"
    set todayRow to 0
    repeat with i from 2 to 200
        if (string value of range ("B" & i) of sheet0fen) = today then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow > 0 then
        set theCell to range ("ZEROFEN_COL" & todayRow) of sheet0fen
        set oldFormula to formula of theCell
        set formula of theCell to oldFormula & "+'1n+'!HABIT_COL_LETTER" & weekRow
        set newVal to string value of theCell
        return "OK: appended +'1n+'!HABIT_COL_LETTER" & weekRow & " to ZEROFEN_COL" & todayRow & " (verify=" & newVal & ")"
    else
        return "ERROR: date TARGET_DATE not found in 0分 col B"
    end if
end tell
```

## Todoist API

Auth header: `Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5`

- Search 0neon: `GET /api/v1/tasks?label=0neon&limit=200`
- Search 1neon: `GET /api/v1/tasks?label=1neon&limit=200`
- Close task: `POST /api/v1/tasks/TASK_ID/close`
- Create task: `POST /api/v1/tasks` with JSON body `{content, labels, project_id, due_date}`

Base URL: `https://api.todoist.com`
