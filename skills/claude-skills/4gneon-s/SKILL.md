---
name: "4gneon-s"
description: "Quarterly Neon scorecard: % of days with all colors, % of days with basic habits logged, avg 1n weekly efficiency, and 2n monthly efficiency. Usage: /4gneon-s [YYYY-Qn]"
user-invocable: true
---

# Quarterly Neon Scorecard (/4gneon-s)

Answers four standing questions for a calendar quarter, reading the live workbook from ix (never the Straylight OneDrive mirror — it desyncs, sometimes by weeks).

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

## Known limitations (by design, not bugs)

- **Day-count denominator excludes future dates.** The sheet pre-fills dates for the rest of the year with blank inputs; `SUM()` over those blank ranges evaluates to `0`, not `None`, so the script filters on `date <= today` rather than trusting Σ being populated. For the current in-progress quarter, percentages are computed over elapsed days only (reported explicitly).
- **Row 88 has no year dimension** — it's a hand-built month-number table that only covers whatever year it was last set up for. The script reads 0n!C1 (a literal year label) and skips metric 4 with a clear message if it doesn't match the requested year, rather than silently reusing a stale year's numbers.
- Requires `ssh ix` to be reachable (the script scp's the live file). If ix is unreachable, it fails loudly rather than falling back to the stale Straylight mirror.

## Dependencies

- Python 3, `openpyxl`
- `ssh ix` configured (Tailscale)
