---
name: "mealplan"
description: "Draft a meal plan through next Thursday from the eating-narrative principles and previous weeks' plans. Usage: /mealplan [notes]"
user-invocable: true
---

# Meal Plan (/mealplan)

Create the coming week's eating plan file in `~/vault/hcbi/hcbc/`, covering
**tomorrow (or today, if invoked before noon) through the NEXT Thursday**. If
today is Thursday or Friday, plan through the Thursday of next week (never a
plan shorter than 4 days).

## Inputs (read all three before drafting)

1. **Principles** — `~/vault/hcbi/hcbc/2026-eating-narrative-May-w4.md`
   (or its successor; the newest `*eating-narrative*` file). Non-negotiables:
   - Sugary drinks are work fuel, budgeted (6/week), not sins.
   - Caffeine is rationed: ~3 caffeine days per 7 (scale to plan length),
     decaf the rest. Name which days are caffeinated in the goal line.
   - Feed umami cravings deliberately (shish tawook, dashi, mushrooms, miso).
   - Every day gets a written default — flex days with no plan go red.
   - Habits strictly healthy; deviations strictly intentional.
2. **Previous plans** — the 2–3 newest `~/vault/hcbi/hcbc/*.w*-eating.md`
   files. Mine them for:
   - The current staple rotation (foods that keep appearing — reuse them).
   - Comments and outcome marks: `🟢🟡🔴`, `(x)`, `(missed)`, bold `**…**`
     day notes. Repeat what went 🟢, retry what was missed (say so in the
     note), drop or rework what went 🔴.
   - The hcbi food journal (/ate 时辰 triads: 卯 AK-AM, 辰 AN-AP, 巳 AQ-AS,
     午 AT-AV, 未 AW-AY, 申 AZ-BB, 戌 BC-BE; date in col B) vs. the plan —
     where actuals diverged repeatedly, plan the actual (it's the revealed
     preference) or name the deviation deliberately.
   - The current table format and goal-line phrasing.
2b. **Household defaults** — `~/vault/hcbi/hcbc/家-meal-system.md`. The
   standing week fixes JM's weekday breakfast (the yogurt-bowl default), the
   weekend breakfasts he cooks, his ordered weekday lunch, and which dinners
   Ashan cooks vs. eating out. Plan JM's Morning/Lunch/Late rows inside that
   frame (e.g. weekday Morning = the breakfast default unless a note says
   otherwise; Mon/Wed/Fri Late = family dinner, 一起 X), and name each
   weekday's lunch order so it can be written into the 家 Meals calendar.
3. **User notes** — anything after `/mealplan` (travel days, dinners out,
   weight goal changes) overrides the defaults.

## Output

Write `~/vault/hcbi/hcbc/YYYY.MM.w#-eating.md` (w# = week-of-month of the
plan's first Sunday; match the existing naming). Frontmatter identical in
shape to the previous plan file. Body:

1. `Previous week: [[<previous file basename>]]` link.
2. `## Plan (<Day M/D> - <Day M/D>)` + one goal line (calorie limit, 分 aim,
   which days are caffeinated).
3. A short "Carried from previous plans" paragraph: 2–4 sentences on what
   worked, what's being retried, what changed.
4. One `### <Day M/D>` section per day using the established table:

   ```
   |         | Food | Drink | 一起  | Who's cooking | Who's eating |
   | ------- | ---- | ----- | --- | ------------- | ------------ |
   | Morning |      |       | X   |             |              |
   | Snack   |      |       |     |             |              |
   | Lunch   |      |       |     |             |              |
   | Late    |      |       | X   |             |              |
   ```

   `一起` = eaten with the kids (Morning and Late usually X). `Who's cooking`
   and `Who's eating` come from the 家 standing week unless a note overrides
   it: weekday Morning = "JM (yogurt bowl); Gi → kids" / "JM + kids", Snack =
   JM / JM, weekday Lunch = "ordered" / JM, Mon/Wed/Fri Late = Ashan / all 7,
   Tue/Thu Late = Ashan / family, weekend breakfast = JM / family, weekend
   lunch and dinner = "eating out" / family. Bold one-line errand/prep notes
   under the day header where needed (hummus run, orders).
5. Mark the previous plan's frontmatter `status: completed` (add
   `updated: <today>`) so only one plan is ever `active`.

## Response Style

Minimal. One confirmation line with the file path and date range, plus at
most 3 bullets on what changed vs. last plan. Do NOT paste the whole plan.
Do NOT ask for confirmation.
