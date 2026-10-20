---
name: "0n"
description: "Mark a 0₦ habit done on a PAST date but credit its points to TODAY. Marks the habit's cell on that date's 0n row, measures the points Excel auto-credited to that day (个 usually; 媒/m5/i9/xk/思 for domain columns; 0g's extra +9), backs them out of that day and appends them to today via the excel-http daemon. Usage: /0n <habit> <date> [value] [--move-only]  e.g. /0n 0l 09.18"
user-invocable: true
---

# /0n — backfill a habit, credit today

`/did <habit> M/D` deliberately does NOT write 0₦ for past dates (it files a
posthoc Todoist task). `/0n` is the explicit opposite: the habit really was
done on that day, the row should say so, but the 分 belong to today because
today is when the work of recording it happened.

## Run

```bash
python3 ~/i446-monorepo/tools/did/0n-backfill.py "<habit>" <date> [value] [--move-only] [--dry-run]
```

- `<habit>`: a 0n row-1 header (`0l`, `0t`, `notes`, `ibx i9`, `早餐`, ...). Case/dash-insensitive.
- `<date>`: `09.18`, `9/18`, `9.18`, `2026-09-18`, or `yesterday`. Must be in the past; for today use `/did`.
- `[value]`: cell value, default `1`.
- `--move-only`: the 0n cell is already marked (by hand, or an earlier run whose 0分 half failed). Skips the 0n write and moves the row-2 weight only.
- `--dry-run`: read-only plan.

Refuses cumulative/variable habits (`问学`, `xk20/22/26`, `冥想`, `o314`, `新闻`, `hiit`, ...): their points are minute- or Toggl-derived and flow through 1n+, not 0n row 2.

## What it does (one run)

1. One AppleScript on Ix: reads the row-2 weight and the target cell, reads 0分 P..Z on the past row, writes the mark, `calculate`, re-reads P..Z. Single script = nothing interleaves.
2. Guard: Σ(measured 0分 change) must equal weight × value (0g may add exactly +9 on 0分!Q). Otherwise the 0n write is reverted and nothing moves.
3. Daemon `batch_append` (ledger `src: "0n backfill <habit> <date>"`): `-N` per changed column on the past date, `+N` on today. Never touches 0分 D/E/F/G:O and never stamps the 0l/0t time in 0n!AF (that would drag the 111 all-colors bonus).
4. Saves the workbook and refreshes the dashboard points cache (same refresher as `/0t`).

## Report

Relay the JSON: `moves` (column, name, pts), `back_out.date`, `credit.date`, and `note` if the move crosses a Sunday-anchored week (that week's 1s totals change). On exit 2 print the `recovery` line verbatim: it is the exact rerun command.

## Not in scope

No Todoist close (the habit's card for that day is long gone). No Toggl. Not a dtd card, so not self-clearing.
