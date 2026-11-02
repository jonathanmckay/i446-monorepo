---
name: "caf"
description: "Use when JM logs caffeine points with /caf, 'caf -60', 'caffeine points' or 'log caf for yesterday'. Writes hcbi's caf column (W) on the day his text names. Not for mg of caffeine (hcbi 'mg cfn' goes through /ate) and not for 0n habits (/did)."
user-invocable: true
---

# Caffeine points (/caf)

Set the `caf` cell of the `hcbi` sheet in Neon for one day. Positive points
are a clean day, negative points are caffeine taken; the number is JM's.

## Usage

```
/caf <points> [when]
```

`when` is free text: nothing (today), `yesterday`, `tomorrow`, `3 days ago`,
a weekday (`friday`, the most recent one; `last friday` never means today),
`10.30` / `10/30` (this year, last year if not yet happened), `2026-10-28`.
Order does not matter: `/caf yesterday -60` and `/caf 10.30 -60` both work.

## Steps

One command, no inference, no confirmation:

```bash
python3 ~/i446-monorepo/tools/ate/caf.py "<arguments verbatim>"
```

It resolves the column through `config/neon-cols.json` (hcbi → caf), finds the
day's row by `M/D` in column B, and SETS the cell through the excel-http
daemon on Ix (journaled). Never call local `osascript`.

## Response Style

Print the script's one line and stop:

```
caf → -60 (2026-10-31, hcbi W306)
caf → 120 (2026-11-01, hcbi W307, was -60.0)
```

`was` means the day already had a value and this replaced it; if that was
not intended, JM re-runs with the right number. An `ERROR:` line (no row for
that date, daemon down, unreadable date phrase) is shown as is; do not retry
with a different date.
