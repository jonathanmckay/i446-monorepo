---
name: "1s"
description: "Weekly strategic review. Copies 1g summary, opens the 1s survey form (daily 0s answers surfaced inline), then compares goals vs time vs points. Usage: /1s"
user-invocable: true
---

# Weekly Strategic Review (/1s)

Compare what you planned (1g goals) vs what you spent time on (Toggl) vs what you achieved (0分 points) across all domains. Writes a review to vault, then marks the 1s task done.

## Usage

```
/1s [week]
/1s survey [week]
```

- No args → reviews the most recent completed week (last Sun–Sat)
- `last` → same as no args
- `MM/DD` → reviews the week containing that date
- `survey [week]` → **survey-only fast path**: just opens the 1s survey form
  for that week (or the last completed week if omitted). Skips the
  completeness gate, MSFT pull, 1g summary copy, Toggl fetch, points
  aggregation, narrative analysis, and vault write — go straight to Step 0b.
  Use this when you just want to fill out the retrospective questions for a
  week (including backfilling an old one) without running the full analysis.

### Survey-only fast path

If the first argument is literally `survey`, skip directly to **Step 0b**
(launch the survey form) using the remaining argument (if any) as the week
date — do not run Step -1's completeness gate, Step 0-pre's MSFT pull, Step
0a's 1g-tldr copy, or Steps 1–8. The survey form itself is unaffected by any
of that (it only reads/writes the `1分+1s` and `0s897` sheets), so none of it
is needed just to fill out the manual questions for a week.

## Steps

### Step -1: Week-completeness gate (BLOCKING)

Skip this step entirely for the `survey` fast path (see above).

The weekly review may not run on an incomplete week. Check first:

```bash
python3 ~/i446-monorepo/tools/1s/1s-survey.py --check-week [YYYY-MM-DD]
```

Exit 0 = clear, proceed. Exit 3 = blockers: missing daily 0s surveys, days
with 0l unmarked, or days with under 20h of Toggl recording. Present the
tool's blocker list verbatim (it names the exact backfill commands — `/0s
<date>`, `/did 0l <M/D>`, `/tg`) and **STOP — do not run any further step**
until the user has backfilled and the check passes. The survey tool enforces
the same gate itself (`--force` is the deliberate escape hatch; use it only
if the user explicitly says to skip backfilling).

### Step 0: Prep — 1g summary, open tabs

Run these prep steps before the analysis.

#### Step 0-pre: MSFT share pull (weekly, must run interactively)

```bash
python3 ~/i446-monorepo/skills/claude-skills/msftshare/msftpull.py
```

Pulls every OneDrive `vault-shared/*.docx`, measures coworker edit volume since
last week, refreshes `.md` sidecars on flipped docs, and flags vault-truth
shadows whose `.docx` has diverged (re-running `/msftshare` on those clobbers
coworker edits). Report: `~/vault/i447/msftshare-pull-report.md`. Echo the
one-line summary; surface any ⚠ clobber flags to the user. This step lives here
(not launchd) deliberately: macOS TCC blocks CloudStorage for launchd-spawned
python3, and /1s already runs weekly in a terminal with full access.

#### Step 0a: Copy 1g tldr to 1分+1s

**Skip this step entirely if the review week is not the most recent completed week** (i.e. the user passed a `week` argument targeting an older week for backfill). The `1g` sheet resets every week (`/1g` overwrites it), so `1g!A1` only ever holds the *current* week's tldr — copying it into an old week's row writes that day's placeholder/garbage text into historical data (regression 2026-08-02: copied "u" into week 6.3's row, ~6 weeks stale). There is no way to recover a past week's tldr from the live sheet; leave the cell as-is for backfills.

Read cell `A1` from the `1g` sheet — this contains the weekly goals summary (tldr). Write it to the `1g summary` column (`D`) in the `1分+1s` sheet at the current week's row (ISO week number for the review week).

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

#### Step 0b: Launch the weekly survey form

Open the 1s survey — a full-screen TUI form (same pattern as `/0s`) that asks
the manual questions of the `1分+1s` row (Title for the Week, Biggest Win,
Biggest missed opportunity, Proud/Regret w/others, Notes). Rating and
High/Low/Avg are NOT asked: those cells hold live formulas computed from the
week's daily surveys (P pulls `'i9'!B{row}`; W/X/Y aggregate `0s897` ⌈/⌊/x̄)
and must never be written. Above each question the form surfaces the week's
DAILY answers from `0s897` (titles, wins, learnings, proud/regret) so
answering is selecting/condensing rather than composing de novo — typing a
day digit (`3`, or `2,5`) as the whole answer expands to that day's text on
save. Finishing the last field (Tab off it, Escape, or a blank Enter) writes
the answers to the review week's row (col A M.W label), saves, and **marks
the weekly 1s task done** (survey completion IS task completion; `--no-mark`
suppresses that for reruns). The write happens while the full-screen form is
still on screen: the status line switches to `Saving to Neon…` for the
AppleScript round trip, then to an explicit `✓ saved to Neon — N field(s)
written` or `✗ save FAILED` right there in the form (2026-08-16 — previously
the form exited immediately and the confirmation only showed up in the plain
terminal after a multi-second gap, which could read as nothing happening at
all). After the form exits, `main()` reprints the per-write detail (and the
`✓ 1s saved to Neon — ...` summary) to the terminal, then (for interactive
form runs, not `--from-json`) auto-closes its own cmux tab ~1s later via
`cmux close-surface` — no stray pane left sitting at a shell prompt. On
failure the pane is left open instead, so the error and any recovery-file
path stay visible.

It needs its own terminal — open it in a new cmux tab (same pattern as `/0s`):

1. ```bash
   cmux new-surface --type terminal
   ```
   Parse the surface and pane refs from the output.
2. ```bash
   cmux respawn-pane --surface surface:<N> --command "python3 ~/i446-monorepo/tools/1s/1s-survey.py [date]"
   ```
   Pass the review-week date through only if the user gave one (`MM/DD` →
   convert to `YYYY-MM-DD`); no arg reviews the last completed Sun–Sat week.
3. ```bash
   cmux focus-pane --pane pane:<N>
   ```
4. Confirm: `1s survey opened in a new cmux tab — daily answers inline, digits pick a day, finishing the last field autosaves.`

Do NOT block on the form — continue with Step 1 while the user fills it. If
cmux is unavailable, tell the user to run it themselves:
`! python3 ~/i446-monorepo/tools/1s/1s-survey.py`

Non-interactive paths (scripting/tests): `--from-json <file>` writes answers
directly; `--print-script` prints the AppleScript without writing;
`--print-context` dumps the fetched daily answers.

### Step 1: Determine the review week

Calculate the Sun–Sat range for the target week. Default: the most recent Saturday and the Sunday before it.

```python
# Example: if today is Sunday 4/20, review week = 4/13 (Sun) – 4/19 (Sat)
```

Set `week_start` (Sunday) and `week_end` (Saturday) as YYYY-MM-DD strings.

**Backfill gate**: if `week_end` is before the most recently completed Saturday
(i.e. the user targeted an old week, not the current one), **STOP after this
step** and tell the user Steps 2/5/6/7 can't run correctly — the `1g` sheet
has no historical archive (same root cause as the Step 0a bug below: `/1g`
overwrites it weekly, so `1g!A1` and its goal rows only ever hold the
*current* week's data). Ask how to proceed: drop the goals/target/Δ columns
and do a time-vs-points-only comparison, abort to survey-only (Step 0b, which
still works — Toggl and 0分 history are real, only `1g` is not), or take
manually-supplied goals from the user. Do not silently pull `1g`'s current
contents and label them as the old week's goals (regression 2026-08-02, week
6.3 backfill).

### Step 2: Pull weekly goals from 1g sheet

Read the `1g` sheet in `Neon分v12.2.xlsx` via AppleScript. For each domain section (i9, m5x2, hcmp, hcb, g245, hci, xk87, hcmc, s897):
- Scan Col A for the domain header
- Read goals from Col D (text), Col E (分 target), Col F (focus bonus), Col G (% done)
- Stop when hitting the next domain header or empty section

Collect into a structure: `{domain: [{goal, fen_target, focus_bonus, pct_done}]}`

### Step 3: Pull Toggl time for the week

Use the toggl_server MCP tool `toggl_date` (the CLI has no `date` command):

```
mcp__toggl_server__toggl_date  date=YYYY-MM-DD
```

Run for each day Sun–Sat (7 calls; they can be batched in parallel). Parse output to get entries with project code and duration. Aggregate by domain (project code):

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

Column map per `vault/g245/CLAUDE.md` (9 columns were removed 2026-04-28;
old Z–AH references are WRONG):

| Column | Domain |
|--------|--------|
| Q | 0g (goals/planning) |
| R | i9 |
| S | m5x2 |
| T | 个 (g245) |
| U | 媒 (hcmc) |
| V | 思 (hcm) |
| W | hcb |
| X | xk (xk87/xk88) |
| Y | 社 (s897) |

Sum each column across the 7 days to get weekly points per domain.

Use AppleScript to read all 7 rows in a single call:

```applescript
tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set s to sheet "0分" of wb
    set results to ""
    -- For each day, find row by date, read Q through Y
    repeat with i from START_ROW to END_ROW
        set bVal to string value of range ("B" & i) of s
        set rowTxt to bVal
        repeat with c in {"Q", "R", "S", "T", "U", "V", "W", "X", "Y"}
            set rowTxt to rowTxt & "|" & (string value of range ((c as string) & i) of s)
        end repeat
        set results to results & rowTxt & "\n"
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

### Step 7b: Stale memory sweep

Scan all `.md` files in the active memory directory (the project-level memory folder loaded into context). For each file with `last_verified:` in frontmatter, check if it's older than 90 days from today. List any stale memories:

```
## Stale memories (>90 days)

| Memory | Last verified | Age | Action |
|--------|--------------|-----|--------|
| QB MCP auth blocked | 2026-04-26 | 93d | Re-verify or remove |
```

For each stale memory, either:
- **Re-verify**: check if the claim is still true, update `last_verified` to today
- **Remove**: if obsolete, delete the file and remove its line from MEMORY.md

Ask the user which action to take for each stale entry.

### Step 8: Report

Show the comparison table and narrative to the user. Do NOT run `/did 1s`
here — the weekly 1s task is marked done by the survey form on `^S` (the
task is not complete until the survey is; user decision 2026-07-21). If the
survey is still open, say so; if it was cancelled, the task stays open until
the user submits it (rerun `1s-survey.py` directly if needed).

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
