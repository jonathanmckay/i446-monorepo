---
name: "push-more"
description: "Log a 'push more' moment: append +30 i9 points to today's row in 0分 and increment the push counter at 0n!X370. Usage: /push-more"
user-invocable: true
---

# Push More (/push-more)

A single ritual write: when you push past the comfortable stopping point, log it.

- **+30 → 0分 today!R** (i9 column)
- **+1 → 0n!X370** (push counter, fixed cell)

Both writes are appended to the existing formula (`=<old>+30`, `=<old>+1`), not overwrites — so repeated invocations stack the same way `/did` does.

## Execution

One AppleScript, one round-trip to Ix. Pipe through `~/.claude/skills/_lib/ix-osa.sh` so the write lands on Ix's Excel instance and never on a local copy that would later merge-conflict via OneDrive.

```bash
~/.claude/skills/_lib/ix-osa.sh <<'OSA'
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set s0fen to sheet "0分" of wb
    set s0n to sheet "0n" of wb

    -- Find today's row in 0分 (date in col B, M/D format)
    set m to ((month of (current date)) * 1) as text
    set d to ((day of (current date)) * 1) as text
    set today to m & "/" & d
    set todayRow to 0
    repeat with i from 2 to 200
        if (string value of cell ("B" & i) of s0fen) = today then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow = 0 then return "ERROR: today not found in 0分 col B"

    -- Append +30 to 0分!R{todayRow} (i9)
    set rCell to cell ("R" & todayRow) of s0fen
    set oldR to formula of rCell
    if oldR = "" or oldR = "0" then
        set formula of rCell to "=0+30"
    else
        set formula of rCell to oldR & "+30"
    end if
    set newR to value of rCell

    -- Append +1 to 0n!X370 (push counter)
    set xCell to cell "X370" of s0n
    set oldX to formula of xCell
    if oldX = "" or oldX = "0" then
        set formula of xCell to "=0+1"
    else
        set formula of xCell to oldX & "+1"
    end if
    set newX to value of xCell

    return "OK: 0分.R" & todayRow & "=" & newR & " | 0n.X370=" & newX
end tell
OSA
```

## Post-write refresh (fire-and-forget)

The dashboard caches `/api/data` for 5 minutes. After the write, ping the refresh hook so the new i9 total shows up on the next render:

```bash
curl -fsS -X POST --max-time 2 http://ix:5558/api/refresh >/dev/null 2>&1 &
disown
```

## Response

One line, terse — surface both new values from the AppleScript output:

```
push-more → i9 +30 (0分.R<row>=<new>) · push +1 (0n.X370=<new>)
```

If the helper exits non-zero (Ix unreachable), surface the error verbatim and do **not** fall back to local `osascript` — local writes cause OneDrive merge conflicts.

## Notes

- No Toggl entry, no Todoist close, no points override. This is a pure two-cell write.
- Both writes are atomic within one AppleScript call, so a partial failure can't leave one cell updated and the other not.
- `0n!X370` is a fixed counter cell — does **not** look up today's row. Different from how `0₦` habit writes work.
