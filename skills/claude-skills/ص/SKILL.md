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

## Argument parsing

Before substituting `N` into the AppleScript, **normalize non-Latin numerals to ASCII digits**. AppleScript's `as number` only coerces `0-9`; passing `٨` (Arabic-Indic) or `八` (CJK) silently fails the write.

| Script | Mapping |
|---|---|
| Arabic-Indic | `٠١٢٣٤٥٦٧٨٩` → `0123456789` |
| Eastern Arabic-Indic (Persian) | `۰۱۲۳۴۵۶۷۸۹` → `0123456789` |
| CJK | `零一二三四五六七八九` → `0123456789`; `十` → `10` |

After normalization, validate with Python `int()` before passing to AppleScript. If parsing fails, abort with `ص: cannot parse <arg> as a number`.

## Execution

**Run on Ix via SSH heredoc** — the Neon workbook is open on Ix. Sheet name is `0n` (not `0₦`). Date column is C (M/D format).

### Increment (+1)

```bash
ssh ix 'osascript <<EOF
tell application "Microsoft Excel"
    set theSheet to sheet "0n" of active workbook
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
        return (val + 1) as text
    else
        return "no row for " & today
    end if
end tell
EOF'
```

### Set to N

```bash
ssh ix 'osascript <<EOF
tell application "Microsoft Excel"
    set theSheet to sheet "0n" of active workbook
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
        return "N"
    else
        return "no row for " & today
    end if
end tell
EOF'
```

Replace `N` with the user's argument.

## Output

One line: `ص: N` (the new value after write).
