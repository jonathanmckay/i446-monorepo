---
name: "4gneon-s"
description: "Quarterly Neon scorecard: all-colors days, basic-habits days, 1n weekly efficiency, 2n monthly efficiency, goal-setting days, 100+ 0g-point days, sub-1000 neon days, and 3n quarterly-task completion. Usage: /4gneon-s [YYYY-Qn]"
user-invocable: true
---

# Quarterly Neon Scorecard (/4gneon-s)

Answers eight standing questions for a calendar quarter, reading the live workbook from ix (never the Straylight OneDrive mirror — it desyncs, sometimes by weeks).

## Execution

```bash
python3 ~/i446-monorepo/tools/4gneon-s/4gneon-s.py [YYYY-Qn]
```

- No argument: defaults to the most recently **completed** quarter (if today is in Q3, that's Q2, not Q3-to-date).
- Explicit quarter: `/4gneon-s 2026-Q2`.

Echo the script's output verbatim. It already handles fetching the fresh file and formatting the report — no additional processing needed.

## What it answers

1. **All colors** — days in the quarter where 0分!F (`∀₦t`, sourced from 0n!AG) is nonzero. AG is a timestamp written by `/allcolors`; nonzero means all colors were hit that day.
2. **Basic habits** — days where 0分!E (`0₦t`, sourced from 0n!AF) is nonzero, i.e. at least something was logged that day.
3. **1n efficiency** — average of 1n+!AN (weekly habit-completion %) over the weeks whose date falls in the quarter. AN is only stamped on each month's anchor week (the `.1` row), so this averages 3 monthly values per quarter, not ~13 weekly ones.
4. **2n efficiency** — 1n+ row 88, a rolling 3-month efficiency table keyed by month number (row 61: col value = month 1-12). Picks the column whose row-61 value equals the quarter's start month (e.g. month 4 for Q2), which for a quarter-start month gives exactly that calendar quarter's window.
5. **Goals set** — days where 0分!Q (`0g`) is at least 9. Q is either 0 or ≥9 in practice (no in-between values observed), so this cleanly separates "set goals that day" from "didn't."
6. **100+ 0g points** — days where 0分!Q is at least 100, a higher bar for "substantial" goal-setting rather than just showing up.
7. **Neon under 1000** — days where 0分!E (`0₦t`) is below 1000.
8. **3n completion** — 1n+ row 101 (the `m5x2` row of the Y:AG quarterly-task table), `<quarter column>101 / AD101`. Quarter columns: Q1=AF, Q2=AG, Q3=AH, Q4=AI — only AF/AG exist as of 2026-07-26; AH/AI get added as the year progresses. Note: the original ask cited column AC as the "Total," but AC is blank throughout this table (verified) — AD is the actual total/weight column, immediately left of AF/AG. Used AD instead.

## Known limitations (by design, not bugs)

- **Day-count denominator excludes future dates.** The sheet pre-fills dates for the rest of the year with blank inputs; `SUM()` over those blank ranges evaluates to `0`, not `None`, so the script filters on `date <= today` rather than trusting Σ being populated. For the current in-progress quarter, percentages are computed over elapsed days only (reported explicitly).
- **Row 88 (2n) and row 101 (3n) have no year dimension** — both are hand-built tables that only cover whatever year/quarters have been set up so far. The script reads 0n!C1 (a literal year label) and skips metrics 4 and 8 with a clear message on a year mismatch, rather than silently reusing stale data. Metric 8 also reports plainly when the quarter's column (AH/AI) doesn't exist yet.
- Requires `ssh ix` to be reachable (the script scp's the live file). If ix is unreachable, it fails loudly rather than falling back to the stale Straylight mirror.

## Dependencies

- Python 3, `openpyxl`
- `ssh ix` configured (Tailscale)
