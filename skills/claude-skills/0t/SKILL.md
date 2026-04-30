---
name: "0t"
description: "Log sleep, refresh dashboard, mark 0t done. Computes last night's sleep from Toggl, writes to 0₦, refreshes the personal dashboard points cache, and marks 0t complete."
user-invocable: true
---

# Daily Time Review (0t)

Compute sleep, refresh dashboard data, mark 0t done. No donut chart; the personal dashboard (localhost:5558) handles visualization.

## Fast Path

Run the script directly:

```bash
python3 ~/i446-monorepo/tools/0t/0t-fast.py [YYYY-MM-DD]
```

Optional date arg = "yesterday" override. Default: actual yesterday.

The script handles everything:
1. Fetches Toggl entries for yesterday + today
2. Computes sleep (睡觉 entries: >=20:00 yesterday + <14:00 today)
3. Writes sleep minutes to 0₦ column D (today's row)
4. Marks 0t done via did-fast.py (0₦ + Todoist + Toggl stop)
5. Saves Excel on Ix, refreshes dashboard points cache

Report the JSON output to the user. Key fields: `sleep_display`, `sleep_write`, `did`, `dashboard`.

## Fallback

If the script fails, fall through to the manual steps below.

### Step 1: Get yesterday's Toggl entries

Use `toggl_date` to fetch entries for `yesterday_date` (YYYY-MM-DD format). If a date is provided (e.g. `3/25`), use it as `yesterday_date`. Otherwise default to actual yesterday.

### Step 2: Compute and log last night's sleep

"Last night" = the sleep bridging `yesterday_date` and `today_date` (n+1).

**2a. Get sleep from yesterday's side (pre-midnight):**
- From yesterday's already-fetched entries, filter for project = `睡觉`
- Keep only entries whose start time is **20:00 or later** (avoids naps)
- Sum their durations in minutes

**2b. Get sleep from today's side (post-midnight):**
- Use `toggl_date` to fetch entries for `today_date`
- Filter for project = `睡觉`
- Keep only entries whose start time is **before 14:00** (avoids next night)
- Sum their durations in minutes

**2c. Total sleep = 2a + 2b**

**2d. Write to 0₦ sheet** via the ix helper (NEVER local osascript):

```bash
~/.claude/skills/_lib/ix-osa.sh <<'AS'
tell application "Microsoft Excel"
    set theSheet to sheet "0n" of workbook "Neon分v12.2.xlsx"

    set m to ((month of (current date)) * 1) as text
    set d to ((day of (current date)) * 1) as text
    set today to m & "/" & d

    -- Find today's row in column C (use range references, not cell(r,c))
    set todayRow to 0
    repeat with r from 3 to 500
        set cellRef to "C" & r
        set cellVal to string value of range cellRef of theSheet
        if cellVal = today then
            set todayRow to r
            exit repeat
        end if
    end repeat

    if todayRow = 0 then
        return "ERROR: date " & today & " not found in column C"
    end if

    -- Write sleep minutes to column D (睡觉)
    set targetRef to "D" & todayRow
    set value of range targetRef of theSheet to SLEEP_MINUTES
    return "OK: wrote SLEEP_MINUTES to " & targetRef
end tell
AS
```

Substitute `SLEEP_MINUTES` with the computed total before piping.

**Note:** The date used for step 2d is always **today** (the actual current date when `/0t` is run), not `yesterday_date`. This is because /0t runs in the morning for yesterday, and last night's sleep is recorded under today.

### Step 3: Mark 0t habit done

Execute the `/did` skill for habit `0t`. Follow the full `/did` flow exactly as if the user had typed `/did 0t`:
- Write `1` to the `0t` column in today's 0₦ row
- Close any active Todoist task labeled `0neon` whose content matches `0t`
- Run Step 2b from the did skill (check if `0l` is also done, write completion time to AF if so)

### Step 4: Confirm

```
Sleep logged: N min → 0₦ D (睡觉), today's row
0t done ✓
```

## Notes

- Sleep column: 0₦ sheet, column D, header = 睡觉
- All Excel writes go through Ix via `~/.claude/skills/_lib/ix-osa.sh`. NEVER write locally.
- If Ix is unreachable: the helper exits non-zero. Surface the failure, do NOT silently mark complete.
- If sleep entries are mislabeled (wrong project), the total will be off; fix in Toggl first.

## Dependencies

- Python 3
- Toggl MCP server configured
- Neon分v12.2.xlsx open in Excel on Ix
