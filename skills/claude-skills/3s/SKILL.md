---
name: "3s"
description: "Quarterly scorecard: fill a 4-4-5 week quarter's row of scorecard.xlsx › 16-26 3s from Neon: 0₲✓ (days with any 0g), sd 0₦ (days 0₦ done before 1000), com0 (days 0₦ logged), 1₦ (avg 1n+ AN), 2₦ (1n+ row 89), and the m5c7 / I9 ratings averaged from /1m5x2 and /1i9. Usage: /3s [YYYY-Qn] [--partial] [--dry-run]"
user-invocable: true
---

# /3s — quarterly scorecard row

```bash
python3 ~/i446-monorepo/tools/3s/3s.py [YYYY-Qn | Qn] [--partial] [--dry-run]
```

- No quarter → the most recently **completed** quarter. Quarters are 13-week
  4-4-5 quarters on the 1n+ fiscal-week ladder (Q3 2026 = weeks 7.1–9.5,
  Sun 7/5 – Sat 10/3), not calendar quarters.
- An in-progress quarter is refused; `--partial` scores it over elapsed days.
- `--dry-run` prints the numbers without writing.

Echo the script's output verbatim. Nothing else to compute.

## What lands where (`scorecard.xlsx` › `16-26 3s`, row = col A `YYYY.Qn`)

| Col | Metric | Source |
|---|---|---|
| B | Timestamp | now |
| D | 0₲✓ | days with any 0g (0分!Q > 0), as `=n/days` |
| E | sd 0₦ | days with 0 < 0分!E < 1000, as `=n/days` |
| F | com0 | days with 0分!E logged, as `=n/days` |
| G | 1₦ | mean of 1n+!AN on the quarter's three month-anchor rows (M.1). **Refuses if any is blank** |
| H | 2₦ | 1n+ row 89 under the quarter's first month (rolling 3-month: Σ row 88 / (D88·3)); blank = 0 |
| I | 3₦ | **manual**, never written |
| K | m5c7 Rating | mean of i9+m5x2!G over the quarter's weeks |
| L | I9 Rating | mean of i9+m5x2!B over the quarter's weeks |

Ratings: MM=1, MA=2, EE=3; OL and blank weeks are skipped. The mean goes to
the nearest letter, with `+`/`-` when it is ≥ 0.25 away from it.

## Failure modes

- `1₦ invariant` → an anchor week's AN is blank in 1n+. Fill it, rerun.
- `0分 has N rows ... expected M` → missing day rows in 0分.
- `appears twice in col A` → duplicate quarter label in the tab; fix the label.
- Ix unreachable → scp of Neon fails loudly (never reads the stale mirror).
- Neon is read from ix's saved file, so unsaved edits in Ix's Excel are not seen.

## Notes

- The tab was renamed `16-23 3s` → `16-26 3s` on 2026-10-05; the script
  renames it again if it ever finds the old name. Roll `SHEET` in `3s.py` when
  the range changes.
- A missing quarter row is appended after the last labelled row.
