---
name: "1s"
description: "Weekly strategic review. Runs /1n donuts, copies 1g summary, opens survey tabs, then compares goals vs time vs points. Usage: /1s"
user-invocable: true
---

# Weekly Strategic Review (/1s)

Compare what you planned (1g goals) vs what you spent time on (Toggl) vs what you achieved (0分 points) across all domains. Writes a review to vault, then marks the 1s task done.

## Usage

```
/1s [week]
```

- No args → reviews the most recent completed week (last Sun–Sat)
- `last` → same as no args
- `MM/DD` → reviews the week containing that date

## Steps

### Step 0: Prep — donuts, 1g summary, open tabs

Run these three prep steps before the analysis.

#### Step 0a: Run /1n (weekly donut charts)

Invoke the `/1n` skill. This generates two donut charts for the review week and inserts them into the `1分+1s` sheet at O{week} and P{week}.

#### Step 0b: Copy 1g tldr to 1分+1s

Read cell `A1` from the `1g` sheet — this contains the weekly goals summary (tldr). Write it to the `1g summary` column (`D`) in the `1分+1s` sheet at the current week's row (same ISO week number used by /1n).

```applescript
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set sheet1g to sheet "1g" of wb
    set sheet1s to sheet "1分+1s" of wb
    set tldr to string value of range "A1" of sheet1g
    set weekRow to WEEK_ROW
    set value of range ("D" & weekRow) of sheet1s to tldr
    return "OK: wrote 1g tldr to D" & weekRow
end tell
```

Replace `WEEK_ROW` with the ISO week number for the review week.

#### Step 0c: Open tabs side-by-side

Use AppleScript to activate the `0s897` sheet in one window and `1分+1s` in another, so the user can fill in the manual survey.

```applescript
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    -- Activate 1分+1s in the current window
    set active sheet of active window to sheet "1分+1s" of wb
    -- Open a new window for the same workbook and show 0s897
    make new window at wb
    set active sheet of active window to sheet "0s897" of wb
end tell
```

After opening both tabs, **pause and tell the user** the tabs are ready for manual survey entry. Wait for them to confirm before continuing to Step 1.

### Step 1: Determine the review week

Calculate the Sun–Sat range for the target week. Default: the most recent Saturday and the Sunday before it.

```python
# Example: if today is Sunday 4/20, review week = 4/13 (Sun) – 4/19 (Sat)
```

Set `week_start` (Sunday) and `week_end` (Saturday) as YYYY-MM-DD strings.

### Step 2: Pull weekly goals from 1g sheet

Read the `1g` sheet in `Neon分v12.2.xlsx` via AppleScript. For each domain section (i9, m5x2, hcmp, hcb, g245, hci, xk87, hcmc, s897):
- Scan Col A for the domain header
- Read goals from Col D (text), Col E (分 target), Col F (focus bonus), Col G (% done)
- Stop when hitting the next domain header or empty section

Collect into a structure: `{domain: [{goal, fen_target, focus_bonus, pct_done}]}`

### Step 3: Pull Toggl time for the week

Use the Toggl CLI:

```bash
python3 ~/i446-monorepo/mcp/toggl_server/toggl_cli.py date YYYY-MM-DD
```

Run for each day Sun–Sat (7 calls). Parse output to get entries with project code and duration. Aggregate by domain (project code):

```python
time_by_domain = {
    "i9": 1350,      # minutes
    "m5x2": 492,
    "hcb": 180,
    ...
}
```

Also compute total tracked time and untracked time (24h × 7 - total - sleep).

### Step 4: Pull 0分 points for the week

Read the `0分` sheet for each day in the week range. For each day's row (found by date in Col B, M/D format), read the domain columns:

| Column | Domain |
|--------|--------|
| Z | 0g (goals/planning) |
| AA | i9 |
| AB | m5x2 |
| AC | 個 (g245) |
| AD | 媒 (hcmc) |
| AF | hcb |
| AG | xk (xk87/xk88) |
| AH | 社 (s897) |

Sum each column across the 7 days to get weekly points per domain.

Use AppleScript to read all 7 rows in a single call:

```applescript
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set s to sheet "0分" of wb
    set results to ""
    -- For each day, find row by date, read Z through AH
    repeat with i from START_ROW to END_ROW
        set bVal to string value of range ("B" & i) of s
        -- read Z, AA, AB, AC, AD, AF, AG, AH
        set zVal to string value of range ("Z" & i) of s
        set aaVal to string value of range ("AA" & i) of s
        set abVal to string value of range ("AB" & i) of s
        set acVal to string value of range ("AC" & i) of s
        set adVal to string value of range ("AD" & i) of s
        set afVal to string value of range ("AF" & i) of s
        set agVal to string value of range ("AG" & i) of s
        set ahVal to string value of range ("AH" & i) of s
        set results to results & bVal & "|" & zVal & "|" & aaVal & "|" & abVal & "|" & acVal & "|" & adVal & "|" & afVal & "|" & agVal & "|" & ahVal & "\n"
    end repeat
    return results
end tell
```

Parse into `points_by_domain = {"i9": 245, "m5x2": 180, ...}`.

### Step 5: Build comparison table

For each domain, compute:
- **Goals set**: count of non-empty goals from 1g
- **分 target**: sum of Col E values for that domain
- **分 actual**: weekly points from 0分
- **Toggl hours**: time_by_domain / 60, rounded to 1 decimal
- **Δ**: (actual - target) / target as percentage
- **Efficiency**: 分 per hour (actual points / toggl hours)

Sort by 分 target descending (biggest priorities first).

### Step 6: Generate narrative analysis

Use Claude to analyze the table and raw data. Prompt:

```
You are reviewing Jonathan's weekly performance data. Be direct and specific.

Week: {week_start} to {week_end}

Goals by domain:
{goals_text}

Comparison table:
{table}

Total tracked time: {total_hours}h
Sleep: {sleep_hours}h

Analyze:
1. **What worked** (2-3 bullets): domains where points met or exceeded targets, especially with efficient time use
2. **What got crowded out** (2-3 bullets): domains where targets were missed despite having goals. Why? (time went elsewhere, meetings, etc.)
3. **Time-points mismatch** (1-2 bullets): domains where lots of time was spent but few points earned (meetings without outcomes) or vice versa (high leverage work)
4. **One priority for next week**: the single highest-leverage adjustment

Keep it under 200 words total. No hedging.
```

### Step 7: Write to vault

Create `~/vault/g245/reviews/YYYY-WNN-1s.md`:

```markdown
---
title: "Week NN Strategic Review"
date: {week_end date}
type: review
tags: [g245, 1s, review]
week: {week_start} – {week_end}
source: /1s
---

## Week {N} ({week_start_short}–{week_end_short})

### Comparison

| Domain | Goals | 分 Target | 分 Actual | Hours | Δ | 分/hr |
|--------|-------|-----------|-----------|-------|---|-------|
| i9     | 3     | 120       | 85        | 22.5  | -29% | 3.8 |
| ...    |       |           |           |       |      |     |
| **Total** | **N** | **T** | **A** | **H** | **Δ%** | **E** |

### Goals Detail

#### i9
- [x] Ship auth migration (50分) — done
- [ ] Review Q2 roadmap (40分) — 60% done
- [x] 1:1 prep for all directs (30分) — done

#### m5x2
...

### Analysis

{narrative from Step 6}
```

Ensure `~/vault/g245/reviews/` directory exists (create if not).

### Step 8: Report + mark done

Show the comparison table and narrative to the user. Then execute `/did 1s` to mark the weekly task complete.

## Notes

- All Excel writes/reads in this skill go through
  `~/.claude/skills/_lib/ix-osa.sh` (pipe AppleScript on stdin). The
  helper executes on Ix and hard-fails if Ix is unreachable. NEVER
  call local `osascript` — local writes cause OneDrive merge
  conflicts against the canonical workbook on Ix.
- Batch the multiple writes in this skill into as few helper calls as
  possible to amortize ssh round-trips.
- The 1g sheet goals reset weekly — read them BEFORE they're overwritten by next week's `/1g`
- Toggl CLI `date` command returns entries for a single day. Must call 7 times.
- 0分 column mapping must match exactly. If columns shift, the review will have wrong data.
- AppleScript calls sequential (no parallel Excel access).
- The `reviews/` folder uses ISO week numbers: `YYYY-WNN` (e.g. `2026-W16`).
