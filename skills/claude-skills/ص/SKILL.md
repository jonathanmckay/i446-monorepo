---
name: "ص"
description: "Log prayers to Neon 0n tab (AM column). No args: +1. With number: set total. Usage: /ص [count]"
user-invocable: true
---

# Prayer Counter (/ص)

Log salah count to Neon spreadsheet, column AM (ص) in the 0n sheet.

## Behavior

- **No arguments** (`/ص`): Increment today's value by 1.
- **With a number** (`/ص 3`): Set today's value to that number (overwrites).

## Execution

**All writes go through `~/.claude/skills/_lib/ix-osa.sh`** (which
runs the AppleScript on Ix via ssh). The Neon workbook lives on Ix.
Sheet name is `0n` (not `0₦`). Date column is C (M/D format). Always
pin the workbook by name (`workbook "Neon分v12.2.xlsx"`) — never
`active workbook`, since a different workbook may be frontmost on Ix.

If Ix is unreachable, the helper hard-fails with a clear error
(exit code 3). Do NOT fall back to local `osascript`; local writes
cause OneDrive merge conflicts.

### Increment (+1)

```bash
~/.claude/skills/_lib/ix-osa.sh <<'AS'
tell application "Microsoft Excel"
    set theSheet to sheet "0n" of workbook "Neon分v12.2.xlsx"
    set m to ((month of (current date)) * 1) as text
    set d to ((day of (current date)) * 1) as text
    set today to m & "/" & d
    set todayRow to 0
    repeat with i from 2 to 200
        if (string value of cell ("C" & i) of theSheet) = today then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow > 0 then
        set theCell to cell ("AM" & todayRow) of theSheet
        set oldVal to string value of theCell
        if oldVal is "" or oldVal is missing value then
            set val to 0
        else
            set val to oldVal as number
        end if
        set value of theCell to (val + 1)
        return "OK: " & ((val + 1) as text)
    else
        return "ERROR: no row for " & today
    end if
end tell
AS
```

### Set to N

```bash
~/.claude/skills/_lib/ix-osa.sh <<'AS'
tell application "Microsoft Excel"
    set theSheet to sheet "0n" of workbook "Neon分v12.2.xlsx"
    set m to ((month of (current date)) * 1) as text
    set d to ((day of (current date)) * 1) as text
    set today to m & "/" & d
    set todayRow to 0
    repeat with i from 2 to 200
        if (string value of cell ("C" & i) of theSheet) = today then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow > 0 then
        set value of cell ("AM" & todayRow) of theSheet to N
        return "OK: N"
    else
        return "ERROR: no row for " & today
    end if
end tell
AS
```

Replace `N` with the user's argument before piping.

## Output

One line: `ص: N` (the new value after write).
