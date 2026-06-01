---
name: "portfoliopnl"
description: "Generate a fund-level P&L summary aggregating all properties in a fund. Usage: /portfoliopnl <fund>"
user-invocable: true
---

# Portfolio P&L Report (/portfoliopnl)

Generate a consolidated fund-level P&L summary by aggregating individual property reports.

## Arguments

```
/portfoliopnl <fund> [YYYY-MM]
```

- `fund`: e.g. `fund-i`, `fund-ii`, `fund-iii`, `fund-iv`, `fund-0`
- Optional date: end month for the trailing 12 months. Defaults to previous full month.

## Execution

### Step 1: Run the portfolio script

```bash
python3 ~/i446-monorepo/tools/pnl/portfolio-pnl-fast.py <fund> --skip-existing [--date YYYY-MM]
```

The script:
1. Runs `pnl-fast.py` for each property in the fund (5s sleep between for rate limiting)
2. Parses each generated report's P&L table
3. Aggregates into a consolidated fund P&L
4. Writes the summary report to `~/vault/m5x2/reports/<quarter>/<fund>/`
5. Prints summary JSON to stdout

Use `--skip-existing` to avoid re-generating reports that were already created today.

### Step 2: Report

Show the summary JSON output. Include the file path so the user can open it.

```
portfoliopnl -> <fund> (<N> properties, <N> units)
  T-12 NOI: $X | Cashflow: $X | DSCR: X.XX
  Weighted Cap: X.XX% | SREO: $XK | Implied: $XK
  Report: <file path>
```
