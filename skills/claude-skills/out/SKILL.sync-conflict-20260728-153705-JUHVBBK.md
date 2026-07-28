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

1. **Write to hcbi AI column** via `~/.claude/skills/_lib/ix-osa.sh`
   (executes the AppleScript on Ix). Do NOT call local `osascript`.

   - Workbook: `Neon分v12.2.xlsx`
   - Sheet: `hcbi`
   - Column: `AI` (idempotently write header "AI" to AI1 on each run)
   - Date column: `B` (M/D format)
   - Find today's row, write the score value

2. **Report** the result.

## AppleScript template

```bash
~/.claude/skills/_lib/ix-osa.sh <<'AS'
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
AS
```

Substitute `M/D` with today's date and `SCORE` with the argument
before piping the heredoc.

## Notes

- All writes go through `ssh ix` via the `_lib/ix-osa.sh` helper. If
  Ix is unreachable the helper exits 3 with a clear error — do NOT
  fall back to local `osascript`. Local writes cause OneDrive merge
  conflicts against the canonical workbook on Ix.
- Neon must be open on Ix.
