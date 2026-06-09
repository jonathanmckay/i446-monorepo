---
name: "pnl"
description: "Generate a trailing 12-month P&L report for an m5x2 property from AppFolio data. Usage: /pnl <property_code> [date]"
user-invocable: true
---

# Property P&L Report (/pnl)

Generate a trailing 12-month income statement for a single property, with Comparisons (MoM, YoY, and budget), a Lease Activity & Exposure matrix, an Equity section, and Historical T-12 NOI.

The Equity section computes **Equity = SREO Value − outstanding debt**, plus LTV, equity %, and trailing-12-month **ROE** and **RORE**. SREO value comes from the q1 sreo tab (col E); outstanding mortgage balances come from the [M5x2 Outstanding Mortgages](https://docs.google.com/spreadsheets/d/1MSQ9wuA2WgMjiE4FrSVJ4v37ncp1ed_J3JKSXe-GzJU/edit) sheet (as of the `DEBT_AS_OF` date in `pnl-fast.py`), summing construction loans / LOC where a property carries more than one note. The lookup tables are hardcoded in the script; refresh them when the source sheets update. A property absent from the mortgages sheet shows equity as unknown.

**ROE / RORE.** Total Return = **T-12 cashflow + T-12 debt paydown (Mortgage Principal) + change in value**. Change in value is computed **two ways**, both vs value one year ago (`SREO_VALUES_1YR_AGO`, from q1 sreo **col C**, "Value Y-1"): **Implied** = T-12 implied value (T-12 NOI ÷ cap rate) − value_1yr; **SREO** = current SREO value (col E) − value_1yr. Each yields its own Total Return, **ROE** (÷ equity one year ago) and **RORE** (÷ realizable equity one year ago), so the chart shows an Implied and a SREO row for each. Equity one year ago is reconstructed from the balance sheet (`value_1yr − (debt_today + T-12 principal paid down)`) rather than rolled back through owner equity payments: AppFolio posts no owner contributions/distributions at the property level (verified empty for the value-add deals), so the capital base is rebuilt from value and debt. The T-12 flows use the clean anchor-based window, matching the Summary.

**No prior-year value (fallback).** For the newest acquisitions with no col-C value (a511, kn47, v202, o155, tc68), there is no change-in-value term, so the section shows a **single** Total Return / ROE / RORE: Total Return = T-12 cashflow + T-12 debt paydown, and equity one year ago is reconstructed as **current equity − debt paydown** (the equity built over the year), with realizable equity one year ago = current realizable equity − debt paydown. Refresh `SREO_VALUES_1YR_AGO` alongside `SREO_VALUES`; once a property has a col-C value it switches automatically to the two-way calc.

All income-statement and budget data is pulled on an **accrual basis** so a closed month's expenses post immediately and all 12 months stay on one consistent method. (Cash basis leaves the most recent closed month income-only until vendor payments clear, which previously forced the anchor back a month.) The Comparisons section therefore anchors on the latest month in the T-12 window (e.g., May for a report run in June), comparing it to the prior month (MoM) and the same month a year earlier (YoY), and adds budget columns: month Budget, Δ Budget (favorable variance), Budget YTD, Actual YTD, and YTD Variance (calendar year-to-date through the anchor month). MoM/YoY deltas are dollar-only.

The Lease Activity & Exposure section shows a current per-unit snapshot (term / MTM / vacant, reconciling to the unit count) plus a monthly matrix of Expired (lease term ends), Renewed (renewal signed), and Acquired (new move-in) for the prior and current calendar years. The prior year is fully known; for the current year, expirations are scheduled for the full year, renewals are shown through roughly one month ahead, and acquisitions through the current month, with later cells left blank.

## Arguments

```
/pnl <property_code> [YYYY-MM-DD]
```

- `property_code`: e.g. `b101`, `s300`, `m405`, `rl16`. Must be in the property registry below.
- Optional date: end date for the trailing 12 months. Defaults to end of prior month (if today is May 31, the T-12 is Jun 2025 through May 2026). Format: `YYYY-MM-DD` or `YYYY-MM`.

## Property Registry

Cap rates sourced from SREO: `q1 sreo` tab, column L, in [2026 Q1 PFS + SREO](https://docs.google.com/spreadsheets/d/1noFafK85LLhd4Umzh84XVOqT3tR42Om0Wf4jBc3ShSU/edit?gid=1380326731).

| Code | Fund | Units | AppFolio ID(s) | Cap Rate | Address |
|------|------|-------|----------------|----------|---------|
| a916 | fund-0 | 12 | 2 | 8.0% | 916 W Augusta Ave, Spokane WA 99205 |
| a210 | fund-0 | 10 | 39 | 7.8% | 4210 N Avalon Rd, Spokane Valley WA 99216 |
| h604 | fund-0 | 1 | 36 | n/a | 604 E Hartson Ave, Spokane WA 99202 |
| m608 | fund-0 | 9 | 35 | 7.5% | 1608 W Main Ave, Spokane WA 99201 |
| p705 | fund-0 | 20 | 47 | 7.5% | 2705 N Pines Rd, Spokane Valley WA 99206 |
| s300 | fund-i | 14 | 8 | 8.3% | 9300 E Sprague Ave, Spokane Valley WA 99206 |
| b101 | fund-i | 17 | 42 | 8.0% | 1010 W Boone Ave, Spokane WA 99201 |
| m405 | fund-i | 26 | 50 | 8.5% | 405 S Maple St, Spokane WA 99201 |
| h731 | fund-i | 8 | 228 | 7.0% | 731 S Hatch St, Spokane WA 99202 |
| hl73 | fund-i | 6 | 49 | 8.0% | 730 N Jackson St, Helena MT 59601 |
| k104 | fund-i | 59 | 220 | 7.0% | 314 N LeFevre St, Medical Lake WA 99022 |
| m221 | fund-i | 14 | 223 | 8.0% | 1221 N Monroe St, Spokane WA 99201 |
| ms22 | fund-i | 8 | 46 | 8.0% | 2252 W Central Ave, Missoula MT 59801 |
| ms43 | fund-i | 15 | 43 | 7.0% | 430 Washington, Missoula MT 59802 |
| ps17 | fund-i | 18 | 222 | 7.8% | 1740 N 5th Ave, Pasco WA 99301 |
| ps25 | fund-i | 44 | 221 | 7.0% | 2524 W Sylvester St, Pasco WA 99301 |
| ps91 | fund-i | 14 | 227 | 7.8% | 908 W Ruby, Pasco WA 99301 |
| w117 | fund-ii | 10 | 225 | 8.0% | 1117 W 5th Ave, Spokane WA 99204 |
| w226 | fund-ii | 10 | 224 | 7.5% | 1226 W 5th Ave, Spokane WA 99204 |
| hl65 | fund-ii | 13 | 44 | 8.5% | 645 N Ewing, Helena MT 59601 |
| c313 | fund-iii | 45 | 603 | 7.0% | 3131 S Cook St, Spokane WA 99223 |
| e328 | fund-iii | 7 | 490 | 7.0% | 3128 E 28th Ave, Spokane WA 99223 |
| j312 | fund-iii | 20 | 521 | 7.0% | 3102 E Jackson Ave, Spokane WA 99207 |
| k308 | fund-iii | 8 | 617 | 7.0% | 308 N Washington St, Medical Lake WA 99022 |
| l912 | fund-iii | 10 | 533 | 6.5% | 912 W Lincoln Pl, Spokane WA 99204 |
| rl16 | fund-iii | 100 | 414 | 7.0% | 1621 George Washington Way, Richland WA 99354 |
| rl21 | fund-iii | 14 | 648 | 8.5% | 2153 Stevens Dr, Richland WA 99354 |
| s129 | fund-iii | 20 | 461 | 7.0% | 12903 E Sprague Ave, Spokane Valley WA 99216 |
| w225 | fund-iii | 25 | 415 | 8.0% | 225 S Wall St, Spokane WA 99201 |
| a511 | fund-iv | 10 | 676 | 7.0% | 511 E Augusta Ave, Spokane WA 99207 |
| kn47 | fund-iv | 18 | 1057,1121,1130 | 6.1% | 4711 W Metaline Ave + 632 N Arthur St, Kennewick WA 99336 |
| o155 | fund-iv | 40 | 1224,1183 | 7.0% | 155 S Oak St + 1905 W 2nd Ave, Spokane WA 99201 |
| tc34 | fund-iv | 20 | 735 | 6.8% | 3401 Pacific Ave, Tacoma WA 98418 |
| tc68 | fund-iv | 176 | 1993 | 6.8% | 6830 Tacoma Mall Blvd, Tacoma WA 98409 |
| v202 | fund-iv | 15 | 719 | 6.5% | 12002 E Valleyway Ave, Spokane Valley WA 99206 |

## Execution

### Step 1: Run the fast path script

```bash
python3 ~/i446-monorepo/tools/pnl/pnl-fast.py <property_code> [--date YYYY-MM]
```

The script handles: AppFolio API calls, GL mapping, derived metrics, historical T-12, comparisons, and validation. Runs in ~3 seconds.

### Step 2: Generate insights

Read the Comparisons section of the report just written. Produce 2-4 bullet-point insights covering:

1. **What moved NOI this month?** Identify the 1-2 GL lines that drove MoM change. Be specific: "R&M Turns dropped from $1,384 to $0" not "OpEx decreased."
2. **YoY trajectory.** Is the property gaining or losing ground vs last year? What's driving the gap?
3. **Risk or anomaly.** Flag anything unusual: DSCR below 1.2, vacancy spikes, R&M > 25% of income, negative cashflow, one-time charges, insurance repricing.
4. **Trend.** Is T-12 NOI rising, falling, or plateauing? How many months of improvement/decline?

Write the insights as a `## Insights` section inserted right after the `## Summary` section (before the main P&L table). Keep each bullet to 1-2 sentences. No hedging, no filler.

### Step 3: Report

Show the summary line plus the insights:
```
pnl → <code> (<fund>, <units> units)
  T-12 NOI: $X | Implied Value: $XK | DSCR: X.XX

  Insights:
  - ...
  - ...
```
