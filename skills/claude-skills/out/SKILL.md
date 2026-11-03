---
name: "out"
description: "Use when JM logs his outdoor score with /out, 'out 10', 'went outside', or 'out -1 yesterday'. Writes hcbi's AI column on the day his text names (today by default). Not for kids' outdoor time (1n+ '1 kids nature') and not for workouts (/hiit)."
user-invocable: true
---

# Outdoor score (/out)

Set the `AI` cell of the `hcbi` sheet in Neon for one day.

## Usage

```
/out <score> [when]
```

Common scores: `10` (went outside), `-1` (didn't). `when` uses the /caf
grammar: nothing (today), `yesterday`, `3 days ago`, a weekday, `10.30` /
`10/30`, `2026-10-28`. Order does not matter: `/out yesterday 10` works.

## Steps

One command, no inference, no confirmation:

```bash
python3 ~/i446-monorepo/tools/ate/out.py "<arguments verbatim>"
```

It resolves the column through `config/neon-cols.json` (hcbi → AI), finds the
day's row by `M/D` in column B, and SETS the cell through the excel-http
daemon on Ix (journaled). Never call local `osascript`.

## Response Style

Print the script's one line and stop:

```
out → 10 (2026-11-03, hcbi AI308)
out → -1 (2026-11-02, hcbi AI307, was 10.0)
```

`was` means the day already had a value and this replaced it. An `ERROR:`
line (no row for that date, daemon down, unreadable date) is shown as is; do
not retry with a different date.
