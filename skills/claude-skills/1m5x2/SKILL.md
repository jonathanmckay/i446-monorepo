---
name: "1m5x2"
description: "Weekly m5x2 (McKay Capital) review. Calculates last week's (Sun-Sat) m5x2 minutes from Toggl and m5x2 points from 0分, asks 3 reflection questions (rating, review of last week, goals for next week), writes them to the m5x2 columns (G-K) of the Neon 'i9+m5x2' sheet's row for that week, and completes the '1 m5x2' weekly habit. Usage: /1m5x2 [YYYY-MM-DD]"
user-invocable: true
---

# Weekly m5x2 Review (/1m5x2)

The m5x2 twin of `/1i9`. Same sheet, same week rows; m5x2 lives in the
columns to the right of i9 (merged 2026-09-27 from the old `m5x2` tab, which
is kept hidden as `m5x2-old`).

## Usage

```
/1m5x2 [YYYY-MM-DD]
```

No arg = the most recently completed **Sunday–Saturday** week. A date backfills
the week containing it.

## The `i9+m5x2` sheet (`Neon分v12.2.xlsx`)

One row per fiscal week, row 3 = `1.1`, never appended to. m5x2 columns:

| Col | Meaning | Source |
|-----|---------|--------|
| A | M.W fiscal week label | verify, don't write |
| G | Rating: `MM` / `MA` / `EE` (missed / met ambition / exceeded) | Question 1 |
| H | Review of the last week | Question 2 |
| I | Goals for next week | Question 3 |
| J | m5x2 points that week (`='1分+1s'!H<row-1>`, formula, don't overwrite) | sheet |
| K | m5x2 minutes tracked that week (Toggl) | computed |

Columns B–F are `/1i9`'s; leave them alone.

## Steps

1. **Week + row:** `python3 ~/.claude/skills/1m5x2/week_calc.py [YYYY-MM-DD]`
   → `week_start<TAB>week_end<TAB>row<TAB>label`.
2. **Minutes:** one `toggl_range` call for the week; sum entries on project
   **m5x2** (id `108359987`) → `weekly_minutes`.
3. **Points (for the report):** read `0分` col **S** (m5) for the 7 dates via
   `ix-osa.sh` and sum → `weekly_points`. Column J already holds the sheet's
   own weekly figure; report both if they differ.
4. **Verify the row:** `string value of cell 1 of row ROW of sheet "i9+m5x2"`
   must equal the label. If not, stop and report.
**Suggest answers from 1g (before asking).** Read, via `ix-osa.sh`:
- the `1g` tab's m5x2 (col A `m5c7`) goals (rows 13–19: col D goal, E 分, F focus bonus, G % done). The 1g tab is overwritten weekly by `/1g`, so it only reflects the review week when you run this before the next `/1g`; if `1g!A1` is a placeholder or the goals are clearly stale, say so instead of using them;
- the previous week's row in `i9+m5x2`, col I (what you said exceeding/next-week would look like);
- the review week's `/1s` doc (`~/vault/g245/reviews/YYYY-M.W-1s.md`, "Goals Detail"), if it exists.

Then, with each question, offer a drafted answer the user can accept with "ok" or edit:
1. **Rating**: compare the week's actual minutes/points and the goals' % done against last week's stated goal; suggest MM/MA/EE with a one-line reason.
2. **Review (col H)**: one sentence naming which 1g goals got done or moved, plus the biggest Toggl blocks.
3. **Next week (col I)**: carry forward unfinished 1g goals, phrased as concrete outcomes.

5. **Ask the 3 questions** in chat: rating (`MM`/`MA`/`EE`), review of last
   week, goals for next week. Offer the week's biggest Toggl m5x2 blocks as the
   suggested review text.
6. **Write** via `ix-osa.sh` (never local osascript): G=rating, H=review,
   I=goals, K=minutes. Do not touch J.
7. **Complete the habit:** `python3 ~/i446-monorepo/tools/did/did-fast.py "1 m5x2"`
   then `did-fast.py --refresh-cache` in the foreground.
8. **Report:**
   ```
   m5x2 week {label} ({week_start}–{week_end})
   Minutes: {weekly_minutes}m ({h}h {m}m)
   Points: {weekly_points}分
   Rating: {rating}
   Written to i9+m5x2!row {row} (cols G-K). 1 m5x2 habit marked done.
   ```
