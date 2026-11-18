---
name: "2neon"
description: "Use when JM runs /2neon, asks what is left on this month's 2₦ monthly checklist, wants a monthly card checked against the Neon 1n+ monthly block, or adds, drops, renames or re-points a monthly row. Not for doing one row's work (/2n time audit, /2n1 1₦ efficiency, /2hci, /2s scorecard), not for weekly 1n habits (/1n, /1nd), and not for marking a card done (/did)."
user-invocable: true
---

# Monthly checklist (/2neon)

The block in Neon `1n+` rows 62-87 is the definition: A = due day of month,
B = name, C = expected minutes, D = 分, E:P = Jan..Dec. Each row owns one
recurring Todoist card labelled `5^2₦`. The helper keeps the two in step.

Run it with the args verbatim and echo its output:

```bash
python3 ~/i446-monorepo/tools/2neon/2neon.py status            # no args: this month, row by row
python3 ~/i446-monorepo/tools/2neon/2neon.py audit [--fix]     # rows vs cards
python3 ~/i446-monorepo/tools/2neon/2neon.py sync [--since YYYY-MM-DD] [--dry-run] [--skip ROW]
```

## Which command

| JM wants | Run |
|---|---|
| What is left this month | `status` |
| A row changed in Excel (day, name, minutes, 分, new row, dropped row) | `audit`, show the findings, then `audit --fix` |
| A card was completed but its cell is blank | `sync` (it also runs nightly on Ix) |
| To do a row that has a skill | the skill `status` prints beside the row |

## Rules the helper enforces

- **Card content is `<col B> (C) [D]`**, due `every <A>th`; day 29 and later
  is `every last day`. A blank C keeps the card's own `(N)`.
- **Two cards keep their own names** because code finds them by name:
  `2n checksum` and `5^2 1 n Efficiency Clearance`
  (`lib/neon/monthly.py` `MONTHLY_ALIASES`). Renaming a row means editing
  col B, then `audit --fix`; if the old name has completion history, add it
  to `MONTHLY_ALIASES` first so `sync` still credits it.
- **A completion credits the month the occurrence was due**, not the month
  it was finished. A deferred copy carries an `M.D` stamp (`/defer` adds it)
  and credits month M.
- **`sync` never overwrites** a cell that already holds a number.
- **Dropping a row**: clear its A, B and D in Excel and delete the card by
  hand. `audit --fix` creates and repairs cards; it never deletes one.

## Not built yet

Rows with no skill are done by hand and closed with `/did`: rent roll,
P&Ls, dealflow, Plan下月特日, 2 hcme, 5^2 g, the three event rows and the
physical ones. `sync` credits them once the card is closed.
