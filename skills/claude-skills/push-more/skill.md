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

Two writes, split by destination sheet.

### 0分 write (daemon)

Writes to the 0分 sheet MUST go through the excel-http daemon on Ix — daemon writes are journaled in an audit ledger with a `src` label and chain-checked. Never raw AppleScript/ix-osa.sh for 0分. Formula-append semantics are handled server-side (empty cell, bare number, existing formula chain all normalized automatically).

```bash
ssh ix "curl -s -X POST localhost:9876/append -H 'Content-Type: application/json' \
    -d '{\"sheet\":\"0分\",\"col\":\"R\",\"date\":\"<M/D>\",\"value\":\"+30\",\"src\":\"push-more bonus\"}'"
```

`<M/D>` = today's date (e.g. `7/28`). The response includes `"chain": "ok"|"broken"|"new"`; if `"chain": "broken"` appears, report it to the user — the cell was edited outside the daemon.

### 0n write (AppleScript)

The 0n counter write stays AppleScript. Pipe through `~/.claude/skills/_lib/ix-osa.sh` so the write lands on Ix's Excel instance and never on a local copy that would later merge-conflict via OneDrive.

```bash
~/.claude/skills/_lib/ix-osa.sh <<'OSA'
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set s0n to sheet "0n" of wb

    -- Append +1 to 0n!X370 (push counter)
    set xCell to cell "X370" of s0n
    set oldX to formula of xCell
    if oldX = "" or oldX = "0" then
        set formula of xCell to "=0+1"
    else
        set formula of xCell to oldX & "+1"
    end if
    set newX to value of xCell

    return "OK: 0n.X370=" & newX
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

One line, terse — surface the new 0分 value from the daemon response and the counter from the AppleScript output:

```
push-more → i9 +30 (0分.R<row>=<new>) · push +1 (0n.X370=<new>)
```

If the daemon call fails or the helper exits non-zero (Ix unreachable), surface the error verbatim and do **not** fall back to local `osascript` — local writes cause OneDrive merge conflicts.

## Notes

- No Toggl entry, no Todoist close, no points override. This is a pure two-cell write.
- The two writes are separate calls (daemon for 0分, AppleScript for 0n). If one succeeds and the other fails, say exactly which cell was updated and which was not.
- `0n!X370` is a fixed counter cell — does **not** look up today's row. Different from how `0₦` habit writes work.
