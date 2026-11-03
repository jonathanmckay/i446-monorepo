---
name: "hiit"
description: "Use when JM logs future-fit workouts with /hiit, 'hiit 1', 'did future fit', or 'hiit 2 yesterday'. Writes the number of future-fit workouts done that day to hcbi's AE column (header 'ff') on the day his text names (today by default). Not the 0n 'hiit' habit card (that is /did hiit, or /0n hiit for a past day)."
user-invocable: true
---

# Future-fit workouts (/hiit)

Set the `ff` cell (column AE) of the `hcbi` sheet in Neon for one day: how
many future-fit workouts JM completed.

## Usage

```
/hiit [count] [when]
```

`count` defaults to nothing: a bare `/hiit` means 1. `when` uses the /caf
grammar: nothing (today), `yesterday`, `3 days ago`, a weekday, `10.30` /
`10/30`, `2026-10-28`. Order does not matter.

## Steps

One command, no inference, no confirmation. If JM gave no number, pass `1`
before his date words:

```bash
python3 ~/i446-monorepo/tools/ate/hiit.py "<count> <when>"
```

It resolves the column through `config/neon-cols.json` (hcbi → ff), finds the
day's row by `M/D` in column B, and SETS the cell through the excel-http
daemon on Ix (journaled). It replaces, not adds: a second workout the same day
is `/hiit 2`. Never call local `osascript`.

## Response Style

Print the script's one line and stop:

```
hiit → 1 (2026-11-03, hcbi AE308)
hiit → 2 (2026-11-03, hcbi AE308, was 1.0)
```

An `ERROR:` line (no row for that date, daemon down, unreadable date) is shown
as is; do not retry with a different date.
