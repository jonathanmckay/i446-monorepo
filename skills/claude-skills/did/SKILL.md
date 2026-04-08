---
name: "did"
description: "Mark habits or tasks as done. Supports multiple items separated by comma/semicolon. Writes to 0₦ (habits) or 0分 (Todoist tasks), completes in Todoist. Usage: /did <habit> [time], <habit2> [time2] [yesterday|M/D]"
user-invocable: true
---

# Mark Habit Done (/finished)

Mark a daily habit as complete in the Neon分v12.2.xlsx spreadsheet (`0₦` sheet) using AppleScript, and complete the matching task in Todoist (tagged `0neon`).

## Usage

```
/did <habit> [time] [date]
/did <habit1> [time1], <habit2> [time2], ... [date]
/did <habit1> [time1]; <habit2> [time2]; ... [date]
```

- `<habit>` — the column header as it appears in row 1 of the `0₦` sheet (e.g. `o314`, `冥想`, `hiit`, `0t`), or a Todoist task name
- `[time]` — optional number to write into the cell (minutes). If omitted, search today's Toggl entries for a matching description (see Step 1b). If no Toggl match, writes `1`.
- `[date]` — optional date applied to **all** items. Can be `yesterday` or a date in `M/D` or `MM/DD` format (e.g. `3/25`). Defaults to today.
- Multiple items can be separated by `,` or `;` — each is processed independently

Examples:
- `/did o314 20` → writes 20 in the o314 column for today
- `/did hiit` → searches Toggl for "hiit" entries today, sums their minutes, writes that
- `/did 冥想 15` → writes 15 in the 冥想 column for today
- `/did slack m5x2, slack github` → marks both slack habits done (writes 1 each)
- `/did push 4; 早餐; day hci` → marks three habits done in one command
- `/did hiit yesterday` → writes hiit for yesterday
- `/did o314 20, 冥想 15 3/25` → writes both habits for 3/25

## Steps

### Step -2: Parse global date

Before doing anything else, inspect the **full raw argument string** (everything after `/did`).

Check if the **last whitespace-delimited token** matches one of:
- `yesterday`
- A pattern like `M/D` or `MM/DD` (e.g. `3/25`, `12/01`)

If it matches, strip that token from the args and resolve it to an M/D string (`targetDate`):
- `yesterday` → subtract 1 day from today, format as `M/D` (e.g. `4/4`)
- `M/D` or `MM/DD` → normalize to `M/D` without leading zeros (e.g. `03/25` → `3/25`)

If no date token is found, set `targetDate` to today's date in `M/D` format.

Carry `targetDate` through all subsequent steps. It replaces every use of "today" in the AppleScripts — substitute it as `TARGET_DATE` before running `osascript`.

### Step -1: Split multiple items

If the input (after stripping the date token) contains `,` or `;`, split into separate items. Trim whitespace from each. Process each item independently through Steps 0–5, then report all results together at the end.

### Step -0.5: Resolve habit aliases

Before matching against 0₦ headers, apply this alias map. If the user's input (after stripping date/project tokens) exactly matches a key, replace it with the value:

| User input | 0₦ column header |
|-----------|------------------|
| `hcmc` | `night hcmc` |
| `stats m5x2` | `stats m5x2` |

This prevents matching the wrong column when multiple headers contain the same substring.

### Step 0: Determine path (habit / Todoist task / variable task)

**First:** Try to match `<habit>` (after alias resolution) against the 0₦ sheet column headers (row 1). If it matches:
- If `targetDate` is **today** → proceed to Step 1 (normal habit flow).
- If `targetDate` is a **past date** → skip Steps 1–3 entirely. Jump to [Step 6b: Posthoc habit](#step-6b-posthoc-habit).

**First-b:** If no 0₦ match, try to match `<habit>` against the **1n+ sheet row 1 headers** (columns C onward, partial/case-insensitive OK). If it matches → jump to [Step 1n: 1neon task](#step-1n-1neon-task).

**Second:** If it does not match any 0₦ column header or 1n+ header, search **all active Todoist tasks** (not just `0neon`-labeled ones) for a fuzzy match against `<habit>`. **Paginate through all pages** using `next_cursor` until exhausted — never stop at the first page. Todoist API order is non-deterministic; a task can appear on any page regardless of due date or name. Fetch page 1 (`?limit=200`), collect results, then keep fetching with `&cursor=<next_cursor>` until `next_cursor` is null.

**Matching algorithm — word overlap:**
1. Tokenize the user input: lowercase, split on whitespace/punctuation, strip bracketed values like `[30]`, drop common stopwords (`a`, `an`, `the`, `with`, `on`, `in`, `for`, `to`, `of`, `and`). Call these the **query words**.
2. For each task returned, tokenize its `content` the same way. Compute the **overlap ratio**: `(# query words found in task tokens) / (# query words)`.
3. A task **matches** if overlap ratio ≥ 0.6 (i.e., ≥60% of query words are present in the task).
4. If multiple tasks match, take the one with the highest overlap ratio.
5. If no task hits 0.6, try 0.4 **only if exactly one task** reaches that threshold (avoids false positives).

**Example:** user says "30m session with lx on claude CLI", task is "30m lx claude CLI session [30]". Query words: `{30m, session, lx, claude, cli}`. Task tokens: `{30m, lx, claude, cli, session}`. Overlap: 5/5 = 1.0 → match.

If a match is found, jump to [Step 5: Todoist-only task](#step-5-todoist-only-task).

> **Why all tasks:** Restricting to `0neon` misses regular one-off tasks (e.g. xk88 sessions, project tasks). Any active task can be marked done via `/did`.
> **Why paginate:** Todoist API order is non-deterministic. Tasks appear in arbitrary order across pages — always exhaust all pages before concluding no match exists.
> **Why word overlap not substring:** Word order varies between user input and task names. Substring match fails when words are reordered.

**Third:** If there is no Todoist match either, treat it as a **variable task** — jump to [Step 6: Variable task](#step-6-variable-task).

### Step 0.3: Detect @project override

After splitting into items (Step -1), check each item for a `@code` token (e.g. `@i9`, `@m5x2`, `@s897`). If found:
- Strip the `@code` token from the item string
- Set `projectOverride` to the code (e.g. `i9`)
- This overrides any inferred category in Step 6 and any Toggl project lookup in Step 5.5

### Step 0.5: Detect time range

After splitting into items (Step -1) but before Step 1, check each item for a `HHMM-HHMM` pattern (e.g. `0940-1030`). The pattern may appear anywhere in the item string.

If found:
- Extract `startTime` and `endTime` (24h, zero-padded to 4 digits)
- Calculate `duration` in minutes (e.g. `0940-1030` → 50 min)
- Remove the time range token from the item string — what remains is the task name
- Set `[time]` to `duration` (used in Steps 1–6 as the minutes/points value)
- Set a flag `hasTimeRange = true` with `startTime`, `endTime`, `duration`

Handle midnight-crossing: if `endTime < startTime`, add 1440 minutes.

### Step 1: Parse arguments

Extract `<habit>` and optional `[time]` from the arguments.

### Step 1b: Auto-detect time from Toggl (if no time provided)

If the user did not provide `[time]`:
- If `targetDate` is **today**, use `toggl_today` to fetch today's entries. Search for entries whose description contains `<habit>` (case-insensitive). Sum the duration in minutes across all matching entries. If matches are found, use that sum as `[time]`. If no matches, fall back to `1`.
- If `targetDate` is **not today** (past date), skip Toggl lookup entirely and fall back to `1`.

Use the `/tg` shortcode mapping to expand the habit name for matching. For example:
- `hiit` → also match Toggl project `hcbp`
- `新闻` → also match Toggl project `hcmc`
- `push` → match description containing "push"

Match on **description** first (substring, case-insensitive). If no description match, try matching the habit name against the Toggl **project code**.

### Step 2: Run AppleScript

Run the following AppleScript via `osascript`. It:
1. Opens the `0₦` sheet
2. Scans row 1 for the column matching `<habit>` (exact match)
3. Scans column C for today's date in M/D format
4. Writes `[time]` into the matching cell

```applescript
tell application "Microsoft Excel"
    -- 0₦ sheet is named "0n" in the workbook; use cell-by-index access (range refs don't work on this sheet)
    set wb to workbook "Neon分v12.2.xlsx"
    set theSheet to sheet "0n" of wb

    -- TARGET_MONTH and TARGET_DAY are integers (e.g. 4 and 7 for April 7)
    set targetMonth to TARGET_MONTH
    set targetDay to TARGET_DAY

    set habitName to "HABIT_PLACEHOLDER"
    set habitCol to 0
    repeat with c from 1 to 60
        set cellVal to value of cell c of row 1 of theSheet
        if cellVal is not missing value then
            set trimmed to do shell script "printf '%s' " & quoted form of (cellVal as text) & " | sed 's/[[:space:]]*$//'"
            if trimmed = habitName then
                set habitCol to c
                exit repeat
            end if
        end if
    end repeat

    if habitCol = 0 then
        return "ERROR: habit '" & habitName & "' not found in row 1"
    end if

    -- Col 3 (C) stores date objects; match by month and day
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

    if todayRow = 0 then
        return "ERROR: date " & targetMonth & "/" & targetDay & " not found in column C"
    end if

    set value of cell habitCol of row todayRow of theSheet to TIME_PLACEHOLDER
    set writtenVal to value of cell habitCol of row todayRow of theSheet
    return "OK: wrote TIME_PLACEHOLDER to col " & habitCol & " row " & todayRow & " (verify=" & (writtenVal as text) & ")"
end tell
```

Before running, substitute:
- `TARGET_MONTH` → month integer from `targetDate` (e.g. `4` for April)
- `TARGET_DAY` → day integer from `targetDate` (e.g. `7` for the 7th)
- `HABIT_PLACEHOLDER` → the habit name from the user's input
- `TIME_PLACEHOLDER` → the time value (number)

Run via:
```bash
osascript -e '...'
```

### Step 2b: Special case — 0l completion time

If the habit is `0l`, check whether `0t` has already been completed today (i.e., the `0t` column in today's row has a non-empty value). If both `0l` and `0t` are done, write the current time as a **4-digit number** (HHMM, 24h format) into column **AF** of today's row in the `0₦` sheet.

For example, if it's 2:34 PM, write `1434`.

Use AppleScript to:
1. Check if the `0t` column for today's row is non-empty
2. If yes, get the current time as HHMM and write it to AF for today's row

```applescript
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set theSheet to sheet "0n" of wb
    set targetMonth to TARGET_MONTH
    set targetDay to TARGET_DAY

    -- Find today's row by date object comparison
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

    if todayRow = 0 then return "SKIP: date not found"

    -- Find 0t column by scanning row 1
    set otCol to 0
    repeat with c from 1 to 60
        set cellVal to value of cell c of row 1 of theSheet
        if cellVal is not missing value then
            set trimmed to do shell script "printf '%s' " & quoted form of (cellVal as text) & " | sed 's/[[:space:]]*$//'"
            if trimmed = "0t" then
                set otCol to c
                exit repeat
            end if
        end if
    end repeat

    if otCol = 0 then return "SKIP: 0t column not found"

    -- Check if 0t is done
    set otVal to value of cell otCol of row todayRow of theSheet
    if otVal is not missing value and (otVal as text) is not "0" and (otVal as text) is not "" then
        -- Write current time as HHMM to 0l completion column (AF = col 32)
        set afCol to 0
        repeat with c from 1 to 60
            set cellVal to value of cell c of row 1 of theSheet
            if cellVal is not missing value then
                set trimmed to do shell script "printf '%s' " & quoted form of (cellVal as text) & " | sed 's/[[:space:]]*$//'"
                if trimmed = "0l⌚" or trimmed = "AF" then
                    set afCol to c
                    exit repeat
                end if
            end if
        end repeat
        -- Fallback: use fixed column index for AF (32)
        if afCol = 0 then set afCol to 32
        set h to hours of (current date)
        set mn to minutes of (current date)
        set timeStr to (h * 100 + mn)
        set value of cell afCol of row todayRow of theSheet to timeStr
        return "OK: wrote " & timeStr & " to col " & afCol & " row " & todayRow
    else
        return "SKIP: 0t not done yet"
    end if
end tell
```

### Step 3: Complete matching Todoist task

Search for an active Todoist task with the label `0neon` whose content matches the habit name (case-insensitive, substring). **Paginate through all pages** using `next_cursor` — same pattern as Step 0:

```bash
# Search for matching task
curl -s "https://api.todoist.com/api/v1/tasks?label=0neon&limit=200" \
  -H "Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5"
```

Filter the JSON results: find a task where `content` contains the habit name (case-insensitive). If found, close it:

```bash
curl -s -X POST "https://api.todoist.com/api/v1/tasks/TASK_ID/close" \
  -H "Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5"
```

If no matching task is found, skip silently (not all habits have a Todoist task).

### Step 2c: Verify 0₦ write

After every AppleScript write to 0₦ or 0分, **check the return value** for `verify=`. If the value is `0`, empty, or clearly wrong (e.g. the written number doesn't appear), flag it as `⚠ checksum mismatch` and report the actual cell value. Do NOT silently report success if the verify value looks wrong.

### Step 4: Report

On success, confirm in one line:
```
<habit> → <time> (today) [+ todoist] ✓ verify=<cell_value>
```

If `targetDate` is today, show `today` instead of the date string. If no Todoist task was found, omit the `[+ todoist]` part. Always include the verify value from the AppleScript return. On error (habit not found, date not found, checksum mismatch), report the error clearly.

### Step 5: Todoist-only task

This step runs when `<habit>` is **not** a 0₦ column header.

1. **Find the task in Todoist.** The task was already found in Step 0 — use that result. (Step 5 is only reached when a Todoist match was found in Step 0.)

2. **Extract points from the task name.** Todoist tasks often have `[N]` in the name (e.g. `check in on f694 (10) [10]`). Parse the number inside `[...]` as the points value. If no `[N]` found, ask the user how many points.

3. **Determine the 0分 column.** Use the task's Todoist labels/tags to map to a 0分 column:
   - Labels containing a known domain code → use the 1nd mapping table (from the `/1nd` skill)
   - Common mappings: `i9`/`i447`/`f693`/`f694` → AA (i9), `m5x2`/`m5` → AB (m5), `g245`/`infra`/`cc` → AC (个), `hcmc` → AD (媒), `xk87`/`xk88`/`xk` → AG (xk), `s897`/`社` → AH (社), `hcb`/`hcbp` → AF (hcb)
   - If ambiguous or no label matches, ask the user which 0分 column.

4. **Append points to today's 0分 row.** Use AppleScript to find today's row in 0分 (date column B, M/D format), then **append** `+N` to the existing formula in the target column. Never overwrite.

```applescript
tell application "Microsoft Excel"
    set theSheet to sheet "0分" of active workbook
    set today to "TARGET_DATE"
    set todayRow to 0
    repeat with i from 2 to 200
        if (string value of cell ("B" & i) of theSheet) = today then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow > 0 then
        set theCell to range ("COL" & todayRow) of theSheet
        set oldFormula to formula of theCell
        set formula of theCell to oldFormula & "+POINTS"
        set newVal to string value of theCell
        return "OK: appended +POINTS to COL" & todayRow & " (verify=" & newVal & ")"
    else
        return "ERROR: date TARGET_DATE not found in 0分 col B"
    end if
end tell
```

5. **Close the Todoist task.**

6. **Report:**
```
✓ <task> → +<points> to <col> (<label>) [todoist closed]
```

### Step 5.5: Create Toggl entry (if time range was provided)

If `hasTimeRange = true`, after completing Step 5 or Step 6, create a Toggl time entry:

- **Description:** the task name (same string used for the habit/task lookup)
- **Start:** `targetDate` at `startTime` (convert HHMM → ISO 8601 with America/Los_Angeles timezone)
- **End:** `targetDate` at `endTime` (same timezone; if end is next day due to midnight crossing, add 1 day)
- **Project:** use the inferred domain code from Step 6 (or the Todoist task's label from Step 5) to look up the Toggl project ID from the project ID map in memory
- Use `toggl_create_entry` from the `toggl_server` MCP tool

If the project code has no entry in the project ID map, create the entry without a project.

Include the Toggl result in the Step 4/6 report line:
```
✓ <task> → +<points> to <col> (<domain>) [variable] + toggl 0940-1030 (50 min)
```

### Step 6: Variable task

This step runs when `<habit>` is neither a 0₦ column header nor a Todoist task. The number provided (e.g. the `45` in `call mom 45`) is treated as **points**, not minutes.

1. **Determine domain category** — if `projectOverride` is set (from Step 0.3), use that code directly. Otherwise, infer from the task description using common sense:
   - Social calls / family / friends → `s897` (AH)
   - Personal relationships / xk → `xk88` or `xk87` (AG)
   - Health / fitness / body → `hcb` (AF)
   - Work / business / McKay Capital → `m5x2` (AB)
   - Tech / infrastructure / code → `i9` (AA)
   - Media / reading / content → `hcmc` (AD)
   - Goals / personal growth → `g245` (AC)
   - Home / family logistics / kids / parenting → `xk87` (AG)
   - When genuinely ambiguous, ask the user before proceeding.

2. **Append points to 0分.** Use the same AppleScript pattern as Step 5, appending `+N` to the inferred column in today's row (or `targetDate`'s row).

3. **Create a posthoc Todoist task and immediately close it.** Since this task has no Todoist presence, create one retroactively so Todoist remains the source of truth for completed work.

   - **Content:** the task name as given, with `@posthoc @YYYY-MM-DD` appended using `targetDate` in ISO format (e.g. `call mom [45] @posthoc @2026-04-04`). The date tag lets the dashboard place the task on the correct day regardless of when it was created.
   - **Labels:** `["posthoc", "<domain-code>"]` (e.g. `["posthoc", "s897"]`)
   - **Project:** use the domain code to pick the matching Todoist project (same mapping used elsewhere in the skill). If no matching project, use Inbox.
   - **Due date:** `targetDate` in ISO format (YYYY-MM-DD) so it appears on the correct day in history

   Create via REST API:
   ```bash
   curl -s -X POST "https://api.todoist.com/api/v1/tasks" \
     -H "Authorization: Bearer TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"content": "TASK_NAME", "labels": ["posthoc", "DOMAIN"], "project_id": "PROJECT_ID", "due_date": "YYYY-MM-DD"}'
   ```

   Then immediately close it:
   ```bash
   curl -s -X POST "https://api.todoist.com/api/v1/tasks/TASK_ID/close" \
     -H "Authorization: Bearer TOKEN"
   ```

4. **Report:**
   ```
   ✓ <task> → +<points> to <col> (<domain>) [variable] [todoist posthoc]
   ```

### Step 1n: 1neon task

This step runs when `<habit>` matches a 1n+ sheet row 1 header. Do **NOT** write to 0₦ at all.

1. **Find the column and current week row in 1n+.**

   Use AppleScript:
   - Scan row 1 of the `1n+` sheet (cols C onward) for a case-insensitive match against `<habit>`
   - Note the column letter (e.g. `K` for "1 -2g")
   - Read the points value from **row 3** of that column (e(分))
   - Find the current week's row by scanning col B for the M.W format date (e.g. `4.1` = April week 1). Compute as: month = current month, weekNum = ceil(day/7). So April 6 → `4.1`.
   - Call this `weekRow`

   ```applescript
   tell application "Microsoft Excel"
       set wb to workbook "Neon分v12.2.xlsx"
       set sheet1n to sheet "1n+" of wb

       set colLetters to {"A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X","Y","Z","AA","AB","AC","AD","AE","AF","AG","AH","AI","AJ","AK","AL","AM","AN","AP"}

       set habitName to "HABIT_PLACEHOLDER"
       set habitCol to 0
       set habitColLetter to ""
       -- Start from C (index 3)
       repeat with c from 3 to count of colLetters
           set colRef to (item c of colLetters) & "1"
           set cellVal to do shell script "printf '%s' " & quoted form of (string value of range colRef of sheet1n) & " | sed 's/[[:space:]]*$//'"
           if cellVal = habitName then
               set habitCol to c
               set habitColLetter to item c of colLetters
               exit repeat
           end if
       end repeat

       if habitCol = 0 then return "ERROR: habit not found in 1n+ row 1"

       -- Get points from row 3
       set pointsVal to string value of range (habitColLetter & "3") of sheet1n

       -- Find weekRow: scan col B for M.W
       set weekRow to 0
       set targetMW to "MW_PLACEHOLDER"
       repeat with r from 4 to 100
           set bVal to string value of range ("B" & r) of sheet1n
           if bVal = targetMW then
               set weekRow to r
               exit repeat
           end if
       end repeat

       if weekRow = 0 then return "ERROR: week row not found for " & targetMW

       -- Write points to 1n+ cell
       set value of range (habitColLetter & weekRow) of sheet1n to (pointsVal as number)

       return "OK: " & habitColLetter & " pts=" & pointsVal & " weekRow=" & weekRow
   end tell
   ```

   Substitute `MW_PLACEHOLDER` with the M.W string for today (e.g. `4.1`). Compute it as: month (no leading zero) + "." + week-of-month (ceil(day/7), no leading zero).

2. **Append cell reference to today's 0分 row.** Map the 1n+ column to the correct 0分 column using `g245/1-neon-meta.md`. Then append `+'1n+'!{colLetter}{weekRow}` (a **cell reference**, not the hardcoded number) to today's 0分 formula:

   ```applescript
   tell application "Microsoft Excel"
       set wb to workbook "Neon分v12.2.xlsx"
       set sheet0fen to sheet "0分" of wb
       set today to "TARGET_DATE"
       set todayRow to 0
       repeat with i from 2 to 200
           if (string value of range ("B" & i) of sheet0fen) = today then
               set todayRow to i
               exit repeat
           end if
       end repeat
       if todayRow > 0 then
           set theCell to range ("ZEROFEN_COL" & todayRow) of sheet0fen
           set oldFormula to formula of theCell
           set formula of theCell to oldFormula & "+'1n+'!HABIT_COL_LETTER" & weekRow
           set newVal to string value of theCell
           return "OK: appended +'1n+'!HABIT_COL_LETTER" & weekRow & " to ZEROFEN_COL" & todayRow & " (verify=" & newVal & ")"
       else
           return "ERROR: date TARGET_DATE not found in 0分 col B"
       end if
   end tell
   ```

   Substitute:
   - `ZEROFEN_COL` → the 0分 column (e.g. `AC` for 個/g245 tasks)
   - `HABIT_COL_LETTER` → the 1n+ column letter found in step 1
   - `weekRow` → the row found in step 1

3. **Find and close the Todoist task.** Search for an active Todoist task labeled `1neon` whose content contains `<habit>` (case-insensitive substring):
   ```bash
   curl -s "https://api.todoist.com/api/v1/tasks?label=1neon&limit=200" \
     -H "Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5"
   ```
   - If found: close it via `POST /api/v1/tasks/TASK_ID/close`.
   - **If not found: report an error** — but still complete steps 1–2. Include in the output: `⚠ no 1neon task found for '<habit>' — points written, Todoist not updated`.

4. **Report:**
   ```
   ✓ <habit> → +<points> to <0fen_col> via '1n+'!{col}{weekRow} [1neon] [+ todoist]
   ```
   Or on Todoist error:
   ```
   ✓ <habit> → +<points> to <0fen_col> via '1n+'!{col}{weekRow} [1neon] ⚠ no 1neon task found in Todoist
   ```

### Step 6b: Posthoc habit

This step runs when `<habit>` matches a 0₦ column header **and** `targetDate` is a past date. No Neon write happens — only a Todoist posthoc record.

1. **Look up the habit's task name.** Search Todoist for an active `0neon`-labeled task whose content contains `<habit>`. Use the content as the task name (e.g. `0l - Daily 分 (8) [20]`). If no active task is found, construct the name as `<habit> [20] @posthoc`.

2. **Create a posthoc Todoist task and immediately close it.**
   - **Content:** task name with `@posthoc @YYYY-MM-DD` appended (ISO format of `targetDate`)
   - **Labels:** `["posthoc", "0neon"]`
   - **Due date:** `targetDate` in ISO format (YYYY-MM-DD)
   - Create via REST API, then immediately close it (same pattern as Step 6).

3. **Report:**
   ```
   ✓ <habit> → posthoc (4/1) [todoist]
   ```

## Notes

- The `0₦` sheet **must be open** in Excel for AppleScript to work. If Excel isn't running or the file isn't open, tell the user to open `~/OneDrive/vault-excel/Neon分v12.2.xlsx`.
- Column headers are in **row 1**. The habit name must match exactly (case-sensitive).
- Date is in column C in **M/D format** (e.g. `3/30`, not `03/30`).
- If the user passes a habit shortcode that looks different from the column header, they need to use the exact header string from row 1.

## Regression tests (documented)

| Input | Expected path | Must NOT happen |
|-------|--------------|-----------------|
| `/did 30m session with lx on claude CLI` — Todoist has task "30m lx claude CLI session [30]" (xk88, no `0neon` label) | Step 0 word-overlap matches it (5/5 words), routes to Step 5, closes it | Must NOT create posthoc duplicate via Step 6 |
| `/did hiit` — no Todoist task at all | Step 6 variable task, infers hcb domain | n/a |
| `/did 0g 2` — matches 0₦ column header, no date | Step 1 habit flow, writes to 0₦ sheet | Must NOT search Todoist |
| `/did 0l 2 4/1` — matches 0₦ column header, past date | Step 6b: posthoc Todoist task for 4/1, no Neon write | Must NOT write to 0₦ sheet |
| `/did some task with words reordered` — Todoist has "words reordered task" | Step 0 word-overlap matches (≥60% words present regardless of order) | Must NOT fall through to Step 6 and create posthoc duplicate |
