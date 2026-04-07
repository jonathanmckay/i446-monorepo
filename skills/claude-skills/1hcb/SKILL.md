---
name: "1hcb"
description: "Weekly nutrition review. Reads food logs from hcbi sheet, gives 6 suggestions, collects feedback, sets goals for next week."
user-invocable: true
---

# Weekly Nutrition Review (/1hcb)

Analyze the past week's food data and generate actionable suggestions for improvement.

## Usage

```
/1hcb
```

## Steps

### Step 1: Read the food log data

Read the hcbi sheet from `~/OneDrive/vault-excel/Neon分v12.2.xlsx` using excel-mcp.

Find the most recent 7 rows with data (non-empty column B dates). Read columns A-BK for those rows.

Key columns to analyze:
- **B**: Date
- **E-N**: Food group servings (bn=beans, br=bread, fr=fruit, cr=crackers, gr=grains, vg=vegetables, fx=flax, g=greens, nt=nuts, sp=spices)
- **O**: Water
- **P**: Protein (g)
- **Q**: Caffeine (mg)
- **R**: Alcohol (g)
- **U**: Calories
- **AL-BG**: Meal breakdowns by time of day (Early Morning, Breakfast, Early Snack, Lunch, Afternoon Snack, Dinner, Late Snack) with food description, calories, protein per meal
- **BH**: Weight
- **BI**: Notes (user reflections)
- **BK**: Previous goals

### Step 2: Read the guide doc for context

Read `~/vault/hcbi/hcbc/0n-hcb-guide.md` for:
- Previous feedback on suggestions (what worked, what didn't)
- Any accumulated context about preferences and constraints

### Step 3: Read previous weeks for patterns

Check 2-3 weeks prior to the current week for trend context (is weight going up/down? are food groups improving?).

### Step 4: Generate 6 suggestions

Based on the data, generate exactly 6 suggestions for next week. Each suggestion should be:
- **Specific** — not "eat more vegetables" but "replace afternoon mocha with chai tea + apple"
- **Actionable** — something that can be done this week
- **Measurable** — can tell if it was done or not
- **Contextual** — based on patterns in the actual data, not generic nutrition advice
- **Prioritized by ROI** — highest impact for lowest effort first

Format each suggestion as:
```
1. [SUGGESTION] — [WHY: data-backed reason]
```

Consider:
- Food group gaps (especially greens, vegetables, fruit)
- Protein adequacy
- Caffeine patterns (tea vs coffee)
- Calorie consistency
- Meal timing and snacking patterns
- What the user's own notes say they're struggling with
- Previous goals and whether they were met
- What previous feedback said worked vs didn't

### Step 5: Present and collect feedback

Show all 6 suggestions to the user. For each, ask:
- Hit (good suggestion) / Miss (not useful) / Skip

Collect any additional context the user provides.

### Step 6: Set next week's goals

Based on the suggestions rated "Hit", propose 2-3 goals for next week. Ask the user to confirm.

### Step 7: Write results

**7a. Append to the guide doc** (`~/vault/hcbi/hcbc/0n-hcb-guide.md`):

Under "## Weekly Reviews", add a new entry:

```markdown
### Week of YYYY-MM-DD

**Data snapshot:** avg calories X, avg protein Xg, weight X lbs
**Food group gaps:** [list lowest-scoring groups]

**Suggestions:**
1. [suggestion] — [Hit/Miss/Skip] [user feedback if any]
2. ...

**Goals for next week:**
- Goal 1
- Goal 2
```

**7b. Optionally write goals to hcbi sheet column BK** for the current week's rows (if user confirms).

### Step 8: Report

Confirm completion:
```
1hcb review complete. N/6 suggestions rated. Goals set for next week.
Guide updated: vault/hcbi/hcbc/0n-hcb-guide.md
```

## Notes

- The hcbi sheet tracks food in a detailed daily format — each row is one day
- Column A is the week number (e.g., 1.1, 1.2), column B is the date (M/D format)
- **The hcb week runs Thursday to Wednesday** (not Sunday to Saturday). When finding "this week's" data, look for the most recent Thursday through the following Wednesday.
- Recent weeks may have sparse data — if the current week has no food logs, look back further
- The user has historically struggled with: leafy greens, replacing coffee with tea, eating vegetables consistently
- Previous goals have focused on: "21 leafy greens per week" and "4 HIIT workouts"
- Use excel-mcp for reading (not AppleScript) since this is read-only analysis
