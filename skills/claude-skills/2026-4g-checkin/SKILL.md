---
name: "2026-4g-checkin"
description: "Quarterly 4g check-in numbers from Neon: all-colors days (0n AG), basic-habit days (0n AF, after backfilling 3000 where 0t+0l are done but AF is blank), 1n done %, 2n+ (2n points / 4500), kids outdoor points (1n+ AL), and hcmc -1 days (0n AV). Defaults to the last completed quarter. Usage: /2026-4g-checkin [YYYY-Qn] [--dry-run]"
user-invocable: true
---

# /2026-4g-checkin — quarterly 4g check-in

First run 2026-10-06 (Q3). Run it again at the end of Q4.

```bash
python3 ~/i446-monorepo/tools/4g-checkin/4g_checkin.py [YYYY-Qn] [--dry-run]
```

- No argument: the most recently completed quarter (run in early January → Q4).
- It **writes** the backfill (AF=3000 on days with 0t and 0l done but AF blank)
  through the excel-http daemon before counting. `--dry-run` reports what it
  would fill without writing.
- Echo the output as a table; no extra analysis unless asked.

## What each number is

| # | Number | Source |
|---|---|---|
| 1 | All colors | days 0n!AG (⎣∀clr) filled |
| 2 | Basic habits | days 0n!AF (N color) filled, after the backfill |
| 3 | 1n done | avg of 1n+!AN over the quarter's month-anchor weeks (`M.1` rows) |
| 4 | 2n+ | Σ 1n+ row 88 for the quarter's three months ÷ 4500 |
| 5 | Kids outdoor | Σ 1n+!AL (1 kids nature) over the quarter's weeks (by `M.W` label month) |
| 6 | hcmc -1 | days 0n!AV (minutes tagged `#-1`) > 0, plus total minutes |

Columns resolve through `config/neon-cols.json`, so a column reshuffle doesn't
break it. Row 88 has no year column: the script refuses item 4 when 0n!C1 is a
different year (extend the sheet for the new year first).
