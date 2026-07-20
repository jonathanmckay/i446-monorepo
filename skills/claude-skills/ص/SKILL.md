---
name: "ص"
description: "Log prayers to Neon 0n tab (AP column). No args: +1. With number: set total. Usage: /ص [count]"
user-invocable: true
---

# Prayer Counter (/ص)

Log salah count to Neon spreadsheet, column AP (ص) in the 0n sheet.

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

**Run on Ix via SSH heredoc** — the Neon workbook is open on Ix. Sheet name is `0n` (not `0₦`). Date column is C.

**Row lookup — bulk-read + date-class-aware (2026-07-20 fix).** Column C holds
real Excel date values, not "M/D" text. The old script did a naive
`(string value of cell (...)) = "M/D"` string compare *inside* a per-row loop —
both wrong (the string comparison silently never matched a real date value,
so the write "failed" with `no row for <date>` even though the row existed)
and slow (~80s+ observed live: up to 500 individual cross-process `cell()`
reads). Fix: one bulk `value of range "C2:C500"` call (a single AppleEvent,
sub-second), then compare each unwrapped value by month/day if it's a real
date, falling back to text match otherwise.

**Workbook — pin explicitly, never `active workbook`.** Ix runs unattended;
if some other file is frontmost, `active workbook` silently targets the wrong
one. Always `workbook "Neon分v12.2.xlsx"`.

**Save — bounded timeout, and never let a slow save swallow the write.** A
save can occasionally hang for 80s+ (observed live, most likely OneDrive's
file-provider being unhealthy) — the value written to the cell is real and
correct well before that. Wrap the save in `with timeout of 15 seconds` so a
hang fails fast, and report the save outcome SEPARATELY from the write
outcome — never let a slow/failed save look like the whole ritual silently
did nothing.

### Increment (+1)

```bash
ssh ix 'osascript <<EOF
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set theSheet to sheet "0n" of wb
    set targetMonth to month of (current date) as integer
    set targetDay to day of (current date)
    set colVals to value of range "C2:C500" of theSheet
    set todayRow to 0
    repeat with i from 1 to (count of colVals)
        set cv to item 1 of (item i of colVals)
        if cv is not missing value then
            try
                if (month of cv as integer) = targetMonth and (day of cv) = targetDay then
                    set todayRow to i + 1
                    exit repeat
                end if
            on error
                try
                    if (cv as text) = ((targetMonth as text) & "/" & (targetDay as text)) then
                        set todayRow to i + 1
                        exit repeat
                    end if
                end try
            end try
        end if
    end repeat
    if todayRow = 0 then return "ERROR: no row for " & targetMonth & "/" & targetDay
    set theCell to cell ("AP" & todayRow) of theSheet
    set oldVal to value of theCell
    if oldVal is missing value or oldVal is "" then
        set val to 0
    else
        set val to oldVal as number
    end if
    set value of theCell to (val + 1)
    set saveStatus to "saved"
    try
        with timeout of 15 seconds
            save wb
        end timeout
    on error errMsg
        set saveStatus to "SAVE_TIMEOUT: " & errMsg
    end try
    return ((val + 1) as text) & "|" & saveStatus
end tell
EOF'
```

### Set to N

```bash
ssh ix 'osascript <<EOF
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set theSheet to sheet "0n" of wb
    set targetMonth to month of (current date) as integer
    set targetDay to day of (current date)
    set colVals to value of range "C2:C500" of theSheet
    set todayRow to 0
    repeat with i from 1 to (count of colVals)
        set cv to item 1 of (item i of colVals)
        if cv is not missing value then
            try
                if (month of cv as integer) = targetMonth and (day of cv) = targetDay then
                    set todayRow to i + 1
                    exit repeat
                end if
            on error
                try
                    if (cv as text) = ((targetMonth as text) & "/" & (targetDay as text)) then
                        set todayRow to i + 1
                        exit repeat
                    end if
                end try
            end try
        end if
    end repeat
    if todayRow = 0 then return "ERROR: no row for " & targetMonth & "/" & targetDay
    set value of cell ("AP" & todayRow) of theSheet to N
    set saveStatus to "saved"
    try
        with timeout of 15 seconds
            save wb
        end timeout
    on error errMsg
        set saveStatus to "SAVE_TIMEOUT: " & errMsg
    end try
    return "N" & "|" & saveStatus
end tell
EOF'
```

Replace `N` with the user's argument.

**Reading the result:** the output is `<value>|<saveStatus>`. If `saveStatus`
starts with `SAVE_TIMEOUT`, the cell write still landed (verify with a
read-back if in doubt) but the save to disk did not complete in time — tell
the user explicitly rather than reporting a clean `ص: N`, and consider
re-running the save on its own once Ix/OneDrive settles.

## Build-order prayer marker (always run after the AP write)

After the Neon write succeeds, stamp the ☀️ صلاة marker on the current 地支 block
in the build order so the prayer shows up in janus, -2n/inbound, wakeup, and the
1-1n heatmap (all of which read ☀️ from the build order, not from Neon AP):

```bash
python3 ~/i446-monorepo/tools/did/prayer_marker.py
```

Run this **locally** (build-order.md lives in the local vault, not on Ix). It is
idempotent — a second prayer in the same block won't duplicate the marker.

## Output

One line: `ص: N` (the new value after write).
