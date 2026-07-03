---
name: "2s"
description: "Copy a month's Neon aggregates into the monthly scorecard (计分卡). Reads 1分+1s AQ:BA for the month, writes scorecard 18-25 2s C:N (I blank). Usage: /2s [month]"
user-invocable: true
---

# Monthly Scorecard Copy (/2s)

Snapshot one month's Neon aggregates into the permanent monthly scorecard.

- **Source:** `Neon分v12.2.xlsx` › sheet `1分+1s`. Monthly aggregates sit at **AQ:BA** on that month's *first-week row* (col A == `<month>.1`).
- **Dest:** `scorecard.xlsx` (计分卡) › sheet `18-25 2s`. Target row = where col A == year AND col B == month.
- **Map** (positional, col **I** `5^1₦` is deprecated → left blank):

  ```
  AQ→C  AR→D  AS→E  AT→F  AU→G  AV→H   [I blank]   AW→J  AX→K  AY→L  AZ→M  BA→N
  ```

  | metric | sd neon | neon | all color | hcmc/d | hcb/d | s897/d | 5^0 g/d | i9 | x2 | -2g | xk87/d |
  |---|---|---|---|---|---|---|---|---|---|---|---|
  | src | AQ | AR | AS | AT | AU | AV | AW | AX | AY | AZ | BA |
  | dst | C | D | E | F | G | H | J | K | L | M | N |

## Why values are read live

The on-disk Neon file lags the live workbook (a month can still be recomputing after the last save), so **values are read live from Excel on Ix**, not from the file. Row numbers are looked up from the files (stable for past months). All Excel I/O runs on Ix via `_lib/ix-osa.sh` (never local osascript — OneDrive-conflict policy). If `scorecard.xlsx` isn't open on Ix, the writer opens it, then saves.

## Steps

1. **Resolve the month.** Argument forms: `2026-05`, `5`, `may`, or omitted → **previous calendar month**. `/2s` is a month-end review, so the default is the month that just ended.

2. **Dry run first — always.**
   ```bash
   python3 ~/.claude/skills/2s/copy2s.py "<month-or-blank>"
   ```
   Prints the resolved month, the source row (1分+1s) and dest row (18-25 2s), and the 11 live values it would write. It warns if the dest row's column C is already non-blank (would overwrite).

3. **Show the user** the resolved month + the 11 values, and confirm before writing. If the dest row is already filled, call that out explicitly.

4. **Write** once confirmed:
   ```bash
   python3 ~/.claude/skills/2s/copy2s.py "<month>" --write
   ```
   Reads AQ:BA live, writes C:N (skipping I) at the dest row, saves `scorecard.xlsx` on Ix.

5. **Report** the month and dest row written.

## Notes

- **Current-year only.** The `1分+1s` sheet holds the current tracking year; col A has no year, so `/2s` matches by month number. A January run (previous month = December of the prior year) is a year-boundary edge case — confirm the source data is actually that month before writing.
- **`#REF!` guard.** If a source metric reads `#REF!` (a broken formula for that month), it copies verbatim — the dry run surfaces it so you can fix Neon first.
- **Scope.** This copies the quantitative row only (`18-25 2s`). The qualitative sheet (`18-24 2s qual`) and the quarterly sheet (`16-23 3s`) are out of scope.
- **Idempotent-ish.** Re-running overwrites the same dest row with current live values; safe to redo if a month's Neon numbers change.
