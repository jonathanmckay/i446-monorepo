---
name: "allcolors"
description: "Stamp completion time of all habit colors. Writes current time (HHMM) to 0n column AG (⎣∀clr) for today's row. Usage: /allcolors"
user-invocable: true
---

# All Colors (/allcolors)

Mark the moment all habit colors are completed for the day. Writes the current time as HHMM into column AG (`⎣∀clr`) of today's row in the `0n` sheet.

## Execution

Pipe the AppleScript below into `~/.claude/skills/_lib/ix-osa.sh` so the write lands on Ix (never local — would cause OneDrive merge conflicts).

```bash
~/.claude/skills/_lib/ix-osa.sh <<'OSA'
tell application "Microsoft Excel"
    set theSheet to sheet "0n" of workbook "Neon分v12.2.xlsx"
    set targetMonth to (month of (current date)) as integer
    set targetDay to day of (current date)
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
    if todayRow = 0 then return "ERROR: today's date not found in 0n col C"
    set h to hours of (current date)
    set mn to minutes of (current date)
    set timeStr to (h * 100 + mn)
    set value of range ("AG" & todayRow) of theSheet to timeStr
    return "OK: wrote " & timeStr & " to AG" & todayRow
end tell
OSA
```

## Response

One line:

```
allcolors → HHMM (AG<row>)
```

Echo the `OK:` value back to the user. No explanation, no confirmation prompt. If the helper exits non-zero, surface the error verbatim.
