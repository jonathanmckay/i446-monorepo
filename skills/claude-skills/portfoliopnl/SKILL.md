---
name: "portfoliopnl"
description: "Generate a fund-level P&L summary aggregating all properties in a fund, or an all-funds portfolio roll-up when called with no fund. Usage: /portfoliopnl [fund]"
user-invocable: true
---

# Portfolio P&L Report (/portfoliopnl)

Generate a consolidated fund-level P&L summary by aggregating individual property reports. **Called with no fund argument, it generates a portfolio-wide roll-up across all five funds.**

## Arguments

```
/portfoliopnl [fund] [YYYY-MM]
```

- `fund` (optional): e.g. `fund-i`, `fund-ii`, `fund-iii`, `fund-iv`, `fund-0`. **Omit (or pass `all`) to generate the all-funds roll-up.**
- Optional date: end month for the trailing 12 months. Defaults to previous full month.

## Execution

### Single fund

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

### All-funds roll-up (no fund argument)

```bash
python3 ~/i446-monorepo/tools/pnl/portfolio-pnl-fast.py [--date YYYY-MM] [--skip-existing]
```

Runs each fund (above) in turn, then aggregates the five fund summaries into one portfolio-wide doc written to `~/vault/m5x2/reports/<quarter>/<date>-all-funds-rollup.md`. The roll-up contains: portfolio totals, a fund breakdown table, an all-properties **NOI vs Budget** table (with z and green/yellow/red flag, sorted worst-z first), a portfolio equity summary, the summed consolidated P&L, and observations. Omitting `--skip-existing` regenerates every underlying property report first (slow; the full daily refresh).

## Report

Show the summary JSON output, including the file path so the user can open it.

```
portfoliopnl -> <fund | all-funds> (<N> properties, <N> units)
  T-12 NOI: $X | Cashflow: $X | DSCR: X.XX
  Weighted Cap: X.XX% | SREO: $XK | Implied: $XK
  Flags: 🟢 X · 🟡 X · 🔴 X   (roll-up only)
  Report: <file path>
```

