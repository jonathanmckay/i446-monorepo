---
name: "2n1"
description: "Use when JM runs /2n1, opens the monthly '5^2 1 n Efficiency Clearance' card, or asks how efficient his weekly 1n habits were last month and which ones cost the most points. Not for marking a weekly 1n habit done (/1n, /1nd), not the monthly Toggl coverage audit (/2n), not the quarterly scorecards that read this number (/3s, /4gneon-s). Usage: /2n1 [month] [--no-mark]"
user-invocable: true
---

# Monthly 1₦ efficiency (/2n1)

Run the helper with the args verbatim and show its output as is (headline,
then the table of habits by 分 missed):

```bash
python3 ~/i446-monorepo/tools/2n1/2n1.py [month] [--no-mark] [--json]
```

`[month]` is `10`, `oct` or `2026-10`. No arg = the last completed month.

## The number

Points earned over points possible across the month's `M.W` rows of Neon
`1n+`:

- **Fixed column** (row 5 is a number): the row-4 分 for each week it was
  done. A binary habit is worth 1x its points; an explicit 0 is not done.
- **Rate column** (row 5 is `1/m`, `.5/m`, `15+1/m`): the cells themselves.
- **Possible:** weeks in the month x the row-4 Σ.

This is the formula already sitting in the `avg` column of each month-anchor
row (`M.1`), which `/3s`, `/4gneon-s` and `/2026-4g-checkin` read. `/2n1`
never writes that cell. It recomputes the figure and prints a `WARNING` when
the cell is blank or disagrees.

| Output | Do |
|---|---|
| `WARNING: ... avg is blank` | Tell JM the anchor row lost its formula; `/3s` will refuse until it is restored. Do not write the cell. |
| `WARNING: ... reads X, not Y` | Report both numbers; the formula's row range is probably off by a week. |
| `card is not due until ...` | Already done this month. Report only. |

## Self-clearing

Bare `/2n1` with the card due today or overdue closes it: did-fast with
`--task-id` (Todoist close + 25 分 to g245 in 0分), then the row's target into
the 1n+ 2₦ block row `1 ₦ efficiency` for the month the card was **due** in
(shared writer `lib/neon/monthly.py`, also used by `/2hci`), then a dtd cache
refresh.

An explicit month, `--no-mark` or `--json` is report-only. Never close the
card for a past month by hand: once the monthly card has advanced, a second
close credits its points twice.
