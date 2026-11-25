---
name: "1s897"
description: "Use when JM runs /1s897, opens the weekly '1 s897' card, or asks to review a week's social time (Toggl s897 and 家 entries against his social priorities). Asks him for his own notes and closes the card once they are in. Not for editing the people database (/s897), not the family and marriage survey (/xk887), not for logging a social event's time (/tg, /do). Usage: /1s897 [M.W or YYYY-MM-DD]"
user-invocable: true
---

# Weekly Social Review (1s897)

Review a week's social activities from Toggl, write raw data to the Neon spreadsheet, add the bot's analysis as 😈 lines, ask JM for his own notes, then close the `1 s897` card.

**Target week**: defaults to the most recent complete week. An optional arg
picks a different week instead: an `M.W` label (e.g. `7.4`) — the canonical
fiscal-week label (Sunday-keyed): fiscal week 1 starts the Sunday on/after
Jan 1, and every 13-week quarter splits into fiscal months of 4, 4, then 5
weeks (the quarter's 3rd month absorbs the extra week). `M` = fiscal month
(1-12), `W` = week within that fiscal month. **This is NOT the same as "which
Sunday falls in calendar month M"** — that simpler rule (still used by
`/did`'s 1n+ Step 1n) silently disagrees at every quarter boundary; see
`week_calc.py`'s docstring for the 2026-08-13 bug this caused. Only 2026 has
a confirmed fiscal-week-1 anchor. Or pass a plain `YYYY-MM-DD` date. Both
resolve to the Wed-Tue week that *contains* the corresponding date (see Step
1). Whichever week gets resolved, Step 4's row lookup writes into *that*
week's row — never just today's.

## Response style

Terse. No preamble. Do the work, report results.

## Steps

### Step 1: Calculate the target week (W-Tu)

Weeks run **Wednesday through Tuesday**. Run the helper — do NOT hand-derive
the dates with ad-hoc `python3 -c` arithmetic (that's exactly what caused the
2026-04-22 Wednesday bug below):

```bash
python3 ~/.claude/skills/1s897/week_calc.py [ARG]
```

Pass the user's argument (if any) verbatim as `[ARG]`:
- **No arg**: the most recent **complete** week. **Always a week that has
  already ended** — never one that includes today (unless today is Tuesday,
  the last day of the week). In particular: on a **Wednesday**, the target
  week ended **yesterday**, NOT the week starting today — this exact
  off-by-one is what caused the 2026-04-22 bug (see the regression test).
  **Do NOT target the week starting today.**
- **`M.W` label** (e.g. `7.4`): the canonical fiscal-week label — `M` = fiscal
  month (1-12), `W` = week within it, per the 4-4-5 quarterly split (see
  above). Resolves to the Wed-Tue week that *contains* that fiscal week's
  Sunday. No year in the label → current year; pass `--year YYYY` to
  override (only 2026 has a confirmed anchor so far).
- **`YYYY-MM-DD`**: the Wed-Tue week that *contains* that date.

The script prints `week_start<TAB>week_end` (both ISO dates); parse that
directly, do not recompute it. It also prints a `WARN:` line to stderr (and
still succeeds) if the resolved week ends in the future — an explicit arg
can legitimately point at an unfinished week, but flag it to the user rather
than silently reviewing a week with partial data.

**Sanity check before writing**: state out loud `today=YYYY-MM-DD (Day), week=Wed YYYY-MM-DD → Tue YYYY-MM-DD`. For the no-arg (default) case, the Tuesday in `week_end` MUST be ≤ today — if `week_end > today`, the script or its input was wrong; recheck before continuing.

### Step 2: Fetch Toggl entries

Use the Toggl MCP server (`toggl_date` tool or equivalent) to fetch all time entries for each day from `week_start` through `week_end`.

Filter to only entries that match **any** of these criteria:
- Project is **s897** (project ID 109719141)
- Project is **家** (project ID 108547409)
- Entry has the **tag `s897`** (shown as `#s897` in toggl_date output) — this captures social physical activities tracked under other projects like hcbp
- Entry has the **tag `家`** (shown as `#家`, possibly comma-joined with other tags like `#xk87,家`) — same idea for family activities tracked under an unrelated project (added 2026-08-13: a `swimming` entry tagged `#xk87,家` under project hcbp fell through the filter entirely since only the `s897` tag was special-cased)

Keep only entries with duration **> 20 minutes**.

### Step 3: Format the activity list

Format each qualifying entry as a bullet:

```
• {description}: {duration_minutes}m ({project_code})
```

Sort by day (chronological). At the end, add a total line:

```
Total: {sum_minutes}m ({hours}h {mins}m)
```

### Step 4: Write raw data to Neon 0s897 sheet

The week's row in the `0s897` sheet of `Neon分v12.2.xlsx` is the one whose
column B date is `week_start` (the Wednesday, top of the week's merged block).

Write the formatted activity list + total to **column AA** ("Event Review") of that row.
Write the total minutes (as a number) to **column Z** ("Time") of that row.

**Important — merged cells**: Columns Z, AA, and AB are merged across each week (7 rows per week, top row is the **Wednesday** = `week_start`). Write to the **top-left cell** of the merged range, i.e. the `week_start` row, never the Tuesday row.

Write through the excel-http daemon (journaled in the neon ledger;
Neon-off-Excel step 2c, 2026-10-30), never raw AppleScript. Save the
formatted list to `/tmp/1s897-aa.txt` with the Write tool (newlines and
quotes kept verbatim), then:

```bash
python3 ~/i446-monorepo/scripts/neon-review-write.py --sheet 0s897 \
  --date <week_start> --merge-col Z --src 1s897 \
  Z=<TOTAL_MINUTES> AA=@/tmp/1s897-aa.txt
```

`--date` finds the `week_start` row by its real date (year-aware, so the
sheet's two Januaries cannot collide); `--merge-col Z` is the sanity check:
it aborts with nothing written (exit 1) unless that row is the top of the
Z merge. Surface any error verbatim; do NOT fall back to local `osascript`.

**Important**: Display the raw data to the user immediately after writing it. The user will be adding their own analysis in parallel.

### Step 5: Read priorities and compare

Read the social priorities from:
- `~/vault/s897/weekly-social-system.md` (weekly scorecard, points budget, quality indicators)
- `~/vault/s897/s897.md` (core philosophy, budgets)

Compare the week's activities against:
1. **Time allocation**: How much time was spent vs. the budget (Social Capital Points: 240/week max, Family = 1pt/min, Self = 0.5pt/min)
2. **Quality indicators**: A/B/C/D week rating based on the criteria (2 in-person things = A week, etc.)
3. **Priorities**: Were the activities aligned with stated goals (consistent quality time, double-booking, prep-driven events)?
4. **Patterns**: Any feast-or-famine signals? Over-indexing on one type?

### Step 6: Write the bot's analysis to Neon (😈 lines)

Column **AB** ("Notes for Improvement / Upcoming") of the same row holds two
voices: JM's own notes as `• ` lines on top, the bot's commentary as `😈 `
lines below. JM does not open Excel (2026-11-25), so the prefix is the only
thing that tells him which lines are his. Never write AB with
`neon-review-write.py` directly: it replaces the whole cell and takes his
notes with it.

Save the analysis to `/tmp/1s897-ab.txt`, one observation per line, no
prefix (the script adds `😈 `):

```
Key observation about the week
What went well and should continue
What to improve next week
Specific suggestion for next week
```

```bash
python3 ~/.claude/skills/1s897/ab_notes.py --date <week_start> --bot @/tmp/1s897-ab.txt
```

It reads the cell, keeps JM's lines, replaces only the `😈 ` lines, writes
through the daemon and prints the cell as it now stands. Exit 1 = nothing
written; surface the error verbatim.

### Step 7: Report, then ask JM for his notes

Give the report:
- Week range (W date - Tu date)
- Activities found (the list)
- Total social time
- Social capital points used
- Week grade (A/B/C/D)
- Top improvement suggestion

If the cell printed by Step 6 already has `• ` lines, show them as "your
notes so far". Then ask, in chat:

> **Notes for this week?**

Wait for the answer. Write it (one thought per line; the script bulletizes
and keeps the 😈 lines):

```bash
python3 ~/.claude/skills/1s897/ab_notes.py --date <week_start> --jm @/tmp/1s897-jm.txt
```

An answer replaces his earlier lines for that week. If he asks to change or
drop the 😈 lines, rerun Step 6's command with the new text (`--bot ""`
removes them all).

| JM's reply | Do |
|---|---|
| Notes | Write them, then Step 8 |
| "none" / "nothing to add" / keeps the notes already there | Write nothing, then Step 8 |
| No reply, or moves to another topic | Write nothing, leave the card open and the claim held |

### Step 8: Close the card (self-clearing)

JM's notes are his part of the survey; once Step 7 has them, the week's
`1 s897` card is done and the skill closes it.

Close **only when both hold**:

1. `/1s897` ran with **no week argument**.
2. The claim hook printed `😈 claimed: 1 s897 … (id <id>)` for this prompt.

```bash
python3 ~/i446-monorepo/tools/did/did-fast.py "1 s897" --task-id <id>
python3 ~/i446-monorepo/tools/did/did-fast.py --refresh-cache
python3 ~/i446-monorepo/tools/did/agent_claims.py release >/dev/null 2>&1
```

Run `--refresh-cache` in the foreground. Then one line: `✓ 1 s897 closed`.

With a week argument, or with no claim (the card is already closed or not on
today's list), stop after Step 7 and say `card left alone`. did-fast credits
the current week's 1n+ cell and today's 0分 whether or not a card is open, so
closing on a backfill or a rerun pays the week twice.

## Example output

```
W 4/1 - Tu 4/7 | 5 activities | 340m (5h 40m)

• Dinner with Dave + Jenna: 120m (s897)
• Call with Forest: 45m (s897)
• Family board game night: 90m (家)
• Zoo with kids + Angela's family: 55m (家)
• Lunch with Stuart: 30m (s897)
Total: 340m (5h 40m)

Written to 0s897 row 98, cols Z + AA.

Points: 255₹ / 240₹ budget (family 210m = 210₹, self 130m = 65₹)
Grade: A (2+ in-person, daily touchpoints unclear from Toggl)

😈 analysis written to col AB.
Top suggestion: Double-book more, zoo + family friends was high-ROI.

Notes for this week?
```

After his answer: `Notes written to col AB. ✓ 1 s897 closed`

## Dependencies

- Toggl MCP server (toggl_date or toggl_today tools)
- Microsoft Excel with Neon分v12.2.xlsx open
- `ab_notes.py` (AB merge, tests in `test_ab_notes.py`), `scripts/neon-review-write.py`
- Files: ~/vault/s897/weekly-social-system.md, ~/vault/s897/s897.md
