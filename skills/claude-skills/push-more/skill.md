---
name: "push-more"
description: "Log a 'push more' moment: append the canonical points (0n!X2, currently 20) to today's row in 0分!Q (g245) and increment the push counter at 0n!X370. Usage: /push-more"
user-invocable: true
---

# Push More (/push-more)

A single ritual write: when you push past the comfortable stopping point, log it.

- **+N → 0分 today!Q** (g245/0g column) — N is read live from **0n!X2**, the
  "push" column's row-2 scoring weight, not hardcoded here. That cell is the
  canonical point value — change it there (not in this file) to change what
  a push is worth. (Changed 2026-09-08 per JM: was a hardcoded +30 to the i9
  column; now reads the weight and posts to g245 instead.)
- **+1 → 0n!X370** (push counter, fixed cell)

The 0分 write is appended to the existing formula (`=<old>+N`), not an overwrite — so repeated invocations stack the same way `/did` does.

## Execution

Two writes, split by destination sheet. Read the weight and bump the counter in the SAME AppleScript call (both touch the 0n sheet), then use that weight in the daemon call.

### 0n: read weight + bump counter (AppleScript)

Pipe through `~/.claude/skills/_lib/ix-osa.sh` so the write lands on Ix's Excel instance and never on a local copy that would later merge-conflict via OneDrive.

```bash
~/.claude/skills/_lib/ix-osa.sh <<'OSA'
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set s0n to sheet "0n" of wb

    -- Canonical point value: row-2 scoring weight under the "push" header.
    set weight to (value of cell "X2" of s0n) as integer

    -- Append +1 to 0n!X370 (push counter)
    set xCell to cell "X370" of s0n
    set oldX to formula of xCell
    if oldX = "" or oldX = "0" then
        set formula of xCell to "=0+1"
    else
        set formula of xCell to oldX & "+1"
    end if
    set newX to value of xCell

    return "OK: weight=" & weight & " 0n.X370=" & newX
end tell
OSA
```

Parse `weight=<N>` from the output — that's the N to use below. If parsing fails or the helper exits non-zero, stop and surface the error; do not fall back to a hardcoded number.

### 0分 write (daemon)

Writes to the 0分 sheet MUST go through the excel-http daemon on Ix — daemon writes are journaled in an audit ledger with a `src` label and chain-checked. Never raw AppleScript/ix-osa.sh for 0分. Formula-append semantics are handled server-side (empty cell, bare number, existing formula chain all normalized automatically).

```bash
ssh ix "curl -s -X POST localhost:9876/append -H 'Content-Type: application/json' \
    -d '{\"sheet\":\"0分\",\"col\":\"Q\",\"date\":\"<M/D>\",\"value\":\"+<N>\",\"src\":\"push-more bonus\"}'"
```

`<M/D>` = today's date (e.g. `7/28`). `<N>` = the weight parsed above. The response includes `"chain": "ok"|"broken"|"new"`; if `"chain": "broken"` appears, report it to the user — the cell was edited outside the daemon.

## Post-write refresh (fire-and-forget)

The dashboard caches `/api/data` for 5 minutes. After the write, ping the refresh hook so the new g245 total shows up on the next render:

```bash
curl -fsS -X POST --max-time 2 http://ix:5558/api/refresh >/dev/null 2>&1 &
disown
```

## Response

One line, terse — surface the new 0分 value from the daemon response and the counter from the AppleScript output:

```
push-more → g245 +<N> (0分.Q<row>=<new>) · push +1 (0n.X370=<new>)
```

If the daemon call fails or the helper exits non-zero (Ix unreachable), surface the error verbatim and do **not** fall back to local `osascript` — local writes cause OneDrive merge conflicts.

## Notes

- No Toggl entry, no Todoist close, no points override. This is a pure two-cell write (plus the read of the weight).
- The two writes are separate calls (AppleScript for 0n, daemon for 0分). If one succeeds and the other fails, say exactly which cell was updated and which was not.
- `0n!X370` is a fixed counter cell — does **not** look up today's row. Different from how `0₦` habit writes work.
- `0n!X2` is the row-2 "scoring weight" cell under the push header — the same convention g245/CLAUDE.md documents for the 0n sheet generally ("Headers in row 1, scoring weights in row 2"). This skill is the one place that actually reads it live; nothing else in the codebase currently derives points from row 2 automatically.
