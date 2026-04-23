---
name: "1g"
description: "Set weekly goals for a domain in the 1g tab of Neon. Estimates points and focus bonus, writes to Excel. Usage: /1g <domain>: <goals>"
user-invocable: true
---

# Set Weekly Goals (/1g)

Set weekly goals for a domain in the `1g` tab of `Neon分v12.2.xlsx`. Estimates 分 (points) and focus bonus for each goal, then writes them to the correct section.

## Usage

```
/1g <domain>: <goals>
```

- `<domain>` — domain code (i9, m5x2, hcmp, hcb, g245, hci, xk87, hcmc, s897)
- `<goals>` — numbered lines, bullet points, or newline-separated goals

Examples:
- `/1g m5x2: 1. Close out Janowski 2. Get answers to March questions 3. AI usage handed off to Matt`
- `/1g i9: SLT prep is solid, Recruiting daily, Ship the dashboard`

## 1g Tab Structure

- Sheet name: `1g` in workbook `Neon分v12.2.xlsx`
- Col A: section headers (domain codes)
- Col D: Goal text
- Col E: 分 (task completion value)
- Col F: Focus Bonus (incentive for completing this week)
- Col G: % Done

### Section Header Rows

| Domain | Header Row | Goal Rows | Col A Value |
|--------|-----------|-----------|-------------|
| i9 | 4 | 5-11 | i9 |
| m5x2 | 12 | 13-19 | m5c7 |
| hcmp | 20 | 21-23 | hcmp |
| hcb | 24 | 25-27 | hcb |
| g245 | 28 | 29-31 | g245 |
| hci | 32 | 33-35 | hci |
| xk87 | 36 | 37 | xk87 |
| hcmc | 38 | 39 | hcmc |
| s897 | 40 | 41-44 | s897 |

**Important:** These row numbers may drift over time. Before writing, scan col A to find the actual row containing the domain header. Use the mapping above as a starting hint, but always verify by reading the sheet.

## Steps

### Step 1: Parse input

Extract `<domain>` (everything before the first `:`) and `<goals>` (everything after).

Parse goals by splitting on:
- Numbered lines (`1.`, `2.`, etc.)
- Bullet points (`-`, `*`)
- Newlines

Strip leading numbers, bullets, whitespace. Each item becomes one goal.

### Step 2: Find section in 1g

Use AppleScript to scan col A (cell index 1) of the `1g` sheet for the domain header. Use this mapping for col A values:

| User domain | Col A value |
|------------|-------------|
| m5x2 | m5c7 |
| hcmp | hcmp |
| (all others) | same as domain code |

Find the header row. Goal rows start at header+1 and continue until the next non-empty col A value (the next section header).

### Step 3: Estimate 分 and focus bonus

For each goal, estimate points using this heuristic:

| Goal type | 分 | Focus Bonus |
|-----------|---|-------------|
| Small discrete task (send email, make call, check in) | 10-20 | 20-40 |
| Medium task (complete deliverable, review, prep) | 30-60 | 50-100 |
| Large strategic goal (close deal, launch, ship) | 80-150 | 100-200 |
| Ongoing/monitoring task (track, monitor, follow up) | 15-30 | 30-60 |

Focus bonus is typically 1.5-2x the 分 value. Harder or more impactful goals get higher multipliers.

Use judgment based on the goal text. When in doubt, lean toward the middle of the range.

### Step 4: Write to 1g sheet

Use AppleScript to write each goal. For each goal at index `i` (0-based):
- Row = headerRow + 1 + i
- Col D (cell 4): goal text
- Col E (cell 5): 分 value
- Col F (cell 6): focus bonus value

Clear any remaining old goal rows in the section (set cols D, E, F to empty) up to the next section header.

```applescript
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set theSheet to sheet "1g" of wb

    -- Find section header row by scanning col A
    set headerRow to 0
    set sectionName to "SECTION_PLACEHOLDER"
    repeat with r from 1 to 50
        set cellVal to value of cell 1 of row r of theSheet
        if cellVal is not missing value then
            set trimmed to do shell script "printf '%s' " & quoted form of (cellVal as text) & " | sed 's/[[:space:]]*$//'"
            if trimmed = sectionName then
                set headerRow to r
                exit repeat
            end if
        end if
    end repeat

    if headerRow = 0 then return "ERROR: section " & sectionName & " not found"

    -- Find next section header (to know where to stop clearing)
    set nextHeader to 50
    repeat with r from (headerRow + 1) to 50
        set cellVal to value of cell 1 of row r of theSheet
        if cellVal is not missing value then
            set trimmed to do shell script "printf '%s' " & quoted form of (cellVal as text) & " | sed 's/[[:space:]]*$//'"
            if trimmed is not "" then
                set nextHeader to r
                exit repeat
            end if
        end if
    end repeat

    -- Write goals (GOAL_WRITES_PLACEHOLDER)

    -- Clear remaining rows
    repeat with r from (headerRow + 1 + GOAL_COUNT) to (nextHeader - 1)
        set value of cell 4 of row r of theSheet to ""
        set value of cell 5 of row r of theSheet to ""
        set value of cell 6 of row r of theSheet to ""
        set value of cell 7 of row r of theSheet to ""
    end repeat

    return "OK: wrote GOAL_COUNT goals to rows " & (headerRow + 1) & "-" & (headerRow + GOAL_COUNT)
end tell
```

Before running, substitute:
- `SECTION_PLACEHOLDER` — the col A value for the domain
- `GOAL_WRITES_PLACEHOLDER` — one write block per goal:
  ```applescript
  set value of cell 4 of row (headerRow + N) of theSheet to "GOAL_TEXT"
  set value of cell 5 of row (headerRow + N) of theSheet to FEN_VALUE
  set value of cell 6 of row (headerRow + N) of theSheet to FOCUS_VALUE
  ```
- `GOAL_COUNT` — number of goals

Pipe the resulting AppleScript through `~/.claude/skills/_lib/ix-osa.sh`
(reads from stdin, runs on Ix). NEVER call local `osascript` —
local writes cause OneDrive merge conflicts against the canonical
workbook on Ix. If Ix is unreachable the helper exits 3 and this
step must surface the failure.

### Step 5: Report

Output a table:

```
1g → <domain> (rows <start>-<end>):
  1. <goal> — <分>分, focus <bonus>
  2. <goal> — <分>分, focus <bonus>
  ...
```

## Response Style

Execute and confirm with the table. Do NOT ask for confirmation before writing. Do NOT explain the estimation process — just show the results.
