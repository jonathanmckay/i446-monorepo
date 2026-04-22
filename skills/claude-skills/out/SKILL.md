---
name: "out"
description: "Log outdoor/outside score to hcbi AI column in Neon. Usage: /out <score>"
user-invocable: true
---

# Log Outdoor Score (/out)

Write a value to the `AI` column in the `hcbi` sheet of Neon for today.

## Usage

```
/out <score>
```

Common values: `10` (went outside), `-1` (didn't go out).

## Response Style

**Minimal output.** Confirm in one line:
```
out → 10 (hcbi AI, row 113)
```

## Steps

1. **Write to hcbi AI column** via `ssh ix 'osascript ...'` (Straylight) or local `osascript`.

   - Workbook: `Neon分v12.2.xlsx`
   - Sheet: `hcbi`
   - Column: `AI` (idempotently write header "AI" to AI1 on each run)
   - Date column: `B` (M/D format)
   - Find today's row, write the score value

2. **Report** the result.

## AppleScript template

```applescript
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set ws to sheet "hcbi" of wb
    -- ensure header
    set value of range "AI1" of ws to "AI"
    -- find today's row
    set today to "M/D"
    set todayRow to 0
    repeat with i from 2 to 200
        if (string value of range ("B" & i) of ws) = today then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow > 0 then
        set value of range ("AI" & todayRow) of ws to SCORE
        return "OK: wrote SCORE to AI" & todayRow
    else
        return "ERROR: date not found"
    end if
end tell
```

Substitute `M/D` with today's date and `SCORE` with the argument.

## Notes

- On Straylight, route through `ssh ix`. Fall back to local with orange terminal if Ix unreachable.
- Neon must be open on Ix.
