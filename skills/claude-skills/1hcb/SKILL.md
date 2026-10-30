---
name: "1hcb"
description: "Weekly health/body review. Survey (lowest weight, what I learned, goals) written to hcbi BG/BH/BJ, plus a food-log analysis with 6 suggestions that inform the goals. Enter on the dtd '1 hcb' card runs the survey alone, under an automated report on the week just ended (hcbp, calories, food groups, year ledger)."
user-invocable: true
---

# Weekly Health Review (/1hcb)

A three-question survey recorded in Neon (hcbi), wrapped around an analysis of
the week's food logs so the goals answer is informed by data.

## Usage

```
/1hcb [YYYY-MM-DD]
```

The date picks the hcb week containing it (default: this week).

**From dtd:** Enter on the `1 hcb` card opens the survey form directly
(`tools/1hcb/1hcb.py`): the weekly report, then the three questions, the
write, and the habit close. No suggestions. That path is for when JM just
wants to record the week.

## The weekly report (printed above the survey)

`1hcb.py` (and `--print-context`) prints a report on the week just ended,
the previous Thursday through Wednesday, read in one `excel.read_range` call
(the excel-http daemon's `/read_range`):

- **hcbp**: week total vs 45分/day (315), days with any hcbp out of 7, and
  s+hcbp (social, `1n+!F`) vs 60 for each fiscal week the hcb week touches.
  Last week's goals (BJ) print just above it for the specific-goal check.
- **cal**: average over logged days vs 1800, days over 1800, protein vs 100g.
- **food**: food 分/day vs 86, Daily Dozen servings vs 21/day, groups short
  of target (worst first) and groups hit.
- **ledger**: the year hcb ledger (hcbi `X384`) at the start and end of the
  week. Rebuilt from the sheet's own formula: per day, food 分 − 86 unless
  the day is unlogged (−50), plus hcbp − 45 unless hcbp is 0, with Excel's
  COUNTIF semantics (text and blanks count).

The report never blocks the survey: a failed read prints
`(report unavailable: …)` and the questions follow.

## The survey and where it lands

| # | Question | hcbi column |
|---|----------|-------------|
| 1 | What was your lowest weight this week? | **BG** (Weight) |
| 2 | What did you learn from this week? | **BH** (Notes) |
| 3 | Goals for this week | **BJ** (Goals (w/hci)) |

All three go on the **Thursday row** that starts the hcb week (the hcb week
runs **Thursday to Wednesday**; every weekly entry since 2026-01-01 sits on a
Thursday row). Answers 2 and 3 are stored as `• ` bullets, one per line, the
sheet's existing style.

`tools/1hcb/1hcb.py` owns the row lookup and the write (through the
excel-http daemon via `lib/neon/excel.py`, journaled in the neon ledger).
Never write these cells by hand or through excel-mcp.

## Steps

### Step 1: Context

```bash
python3 ~/i446-monorepo/tools/1hcb/1hcb.py [YYYY-MM-DD] --print-context
```

Shows the week's Thursday, last week's lowest weight and goals, the weekly
report (see above), and anything already recorded for this week (if so,
confirm before overwriting). Use the report's numbers in Step 5 instead of
recomputing them.

### Step 2: Survey questions 1 and 2

Ask in chat, one message:
1. What was your lowest weight this week?
2. What did you learn from this week?

### Step 3: Read the food log data

Read the hcbi sheet (reads only: excel-mcp on
`~/OneDrive/vault-excel/Neon分v12.2.xlsx`, or `lib/neon/excel.py`) for the
hcb week's 7 day rows (Thu-Wed). Column map (row 1 headers, verified
2026-10-22):

- **A**: week label (周), **B**: date
- **D**: ∑ (food points)
- **E-N**: food group servings, Daily Dozen (bn beans 3, br berries 1, fr fruit 3, cr cruciferous 1, gr grains 3, vg vegetables 2, fx flax 1, g greens 2, nt nuts 1, sp spices 1; with water 3 they sum to the score's 21)
- **O**: water, **P**: protein (g), **Q**: caffeine (mg), **R**: alcohol (g)
- **U**: calories
- **AK-BE**: meals by 地支 block, three columns each (卯 AK, 辰 AN, 巳 AQ, 午 AT, 未 AW, 申 AZ, 戌 BC): food, calories, protein
- **BG**: weight, **BH**: notes, **BJ**: goals (Thursday rows only)

### Step 4: Read the guide and previous weeks

- `~/vault/hcbi/hcbc/0n-hcb-guide.md`: previous feedback on suggestions (what worked, what didn't), preferences and constraints.
- The 2-3 prior Thursday rows (BG weight trend, BH notes, BJ goals) and their weeks' food data for trend context.

### Step 5: Generate 6 suggestions

Exactly 6, each:
- **Specific**: not "eat more vegetables" but "replace afternoon mocha with chai tea + apple"
- **Actionable** this week, **measurable** (done or not)
- **Contextual**: from patterns in the actual data and JM's answer to question 2, not generic advice
- **Ordered by ROI**: highest impact for lowest effort first

```
1. [SUGGESTION] — [WHY: data-backed reason]
```

Consider food group gaps (greens, vegetables, fruit), protein, caffeine (tea vs
coffee), calorie consistency, meal timing and snacking, whether last week's BJ
goals were met, and what the guide says worked.

### Step 6: Feedback and survey question 3

Show the 6 suggestions; for each ask Hit / Miss / Skip. Then ask question 3,
**Goals for this week**, proposing 2-3 goals drawn from the Hits for JM to
accept or rewrite.

### Step 7: Write

**7a. Neon (the survey):**

```bash
python3 ~/i446-monorepo/tools/1hcb/1hcb.py [YYYY-MM-DD] \
  --weight "<lowest weight>" --learned "<answer 2, one item per line>" --goals "<answer 3, one item per line>"
```

Writes BG/BH/BJ on the Thursday row and completes the `1 hcb` habit (pass
`--no-mark` to skip that). A blank answer leaves its cell untouched. If the
write fails the script prints the answers back; rerun it, never hand-write
the cells.

**7b. Guide doc:** append under "## Weekly Reviews" in
`~/vault/hcbi/hcbc/0n-hcb-guide.md`:

```markdown
### Week of YYYY-MM-DD

**Lowest weight:** X lbs
**Data snapshot:** avg calories X, avg protein Xg
**Food group gaps:** [lowest-scoring groups]
**Learned:** [answer 2]

**Suggestions:**
1. [suggestion] — [Hit/Miss/Skip] [feedback if any]
2. ...

**Goals for this week:**
- Goal 1
- Goal 2
```

### Step 8: Report

```
1hcb: BG 191.4 · BH 3 notes · BJ 2 goals → hcbi 10/22 · 1 hcb ✓
N/6 suggestions rated · guide updated
```

## Notes

- Recent weeks may have sparse food logs; if the week has none, say so and look back further for the analysis (the survey still runs).
- Long-running struggles: leafy greens, replacing coffee with tea, vegetables consistently, cardio while travelling.
- The hcb week (Thu-Wed) differs from the Neon 1n+ fiscal week (Sun-Sat); the `1 hcb` habit close goes through /did, which handles its own week.
