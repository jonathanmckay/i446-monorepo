---
name: "pnl"
description: "Generate a trailing 12-month P&L report for an m5x2 property from AppFolio data. Usage: /pnl <property_code> [date]"
user-invocable: true
---

# Property P&L Report (/pnl)

Generate a trailing 12-month income statement for a single property, with Comparisons (MoM + YoY) and Historical T-12 NOI.

## Arguments

```
/pnl <property_code> [YYYY-MM-DD]
```

- `property_code`: e.g. `b101`, `s300`, `m405`, `rl16`. Must be in the property registry below.
- Optional date: end date for the trailing 12 months. Defaults to end of prior month (if today is May 31, the T-12 is Jun 2025 through May 2026). Format: `YYYY-MM-DD` or `YYYY-MM`.

## Property Registry

| Code | Fund | Units | AppFolio ID(s) | Cap Rate | Address |
|------|------|-------|----------------|----------|---------|
| a916 | fund-0 | 12 | 2 | 8.0% | 916 W Augusta Ave, Spokane WA 99205 |
| a210 | fund-0 | 10 | 39 | 8.0% | 4210 N Avalon Rd, Spokane Valley WA 99216 |
| h604 | fund-0 | 1 | 36 | 8.0% | 604 E Hartson Ave, Spokane WA 99202 |
| m608 | fund-0 | 9 | 35 | 8.0% | 1608 W Main Ave, Spokane WA 99201 |
| s300 | fund-i | 14 | 8 | 8.0% | 9300 E Sprague Ave, Spokane Valley WA 99206 |
| b101 | fund-i | 17 | 42 | 8.0% | 1010 W Boone Ave, Spokane WA 99201 |
| m405 | fund-i | 26 | 50 | 8.0% | 405 S Maple St, Spokane WA 99201 |
| w117 | fund-ii | 10 | 225 | 8.0% | 1117 W 5th Ave, Spokane WA 99204 |
| w226 | fund-ii | 10 | 224 | 8.0% | 1226 W 5th Ave, Spokane WA 99204 |
| hl65 | fund-ii | 13 | 44 | 8.0% | 645 N Ewing, Helena MT 59601 |
| c313 | fund-iii | 45 | 603 | 7.0% | 3131 S Cook St, Spokane WA 99223 |
| e328 | fund-iii | 7 | 490 | 7.0% | 3128 E 28th Ave, Spokane WA 99223 |
| j312 | fund-iii | 20 | 521 | 7.0% | 3102 E Jackson Ave, Spokane WA 99207 |
| k308 | fund-iii | 8 | 617 | 7.0% | 308 N Washington St, Medical Lake WA 99022 |
| l912 | fund-iii | 10 | 533 | 7.0% | 912 W Lincoln Pl, Spokane WA 99204 |
| rl16 | fund-iii | 100 | 414 | 7.0% | 1621 George Washington Way, Richland WA 99354 |
| rl21 | fund-iii | 14 | 648 | 7.0% | 2153 Stevens Dr, Richland WA 99354 |
| s129 | fund-iii | 20 | 461 | 7.0% | 12903 E Sprague Ave, Spokane Valley WA 99216 |
| w225 | fund-iii | 25 | 415 | 7.0% | 225 S Wall St, Spokane WA 99201 |
| a511 | fund-iv | 10 | 676 | 7.0% | 511 E Augusta Ave, Spokane WA 99207 |
| kn47 | fund-iv | 18 | 1057,1121,1130 | 7.0% | 4711 W Metaline Ave + 632 N Arthur St, Kennewick WA 99336 |
| o155 | fund-iv | 40 | 1224,1183 | 7.0% | 155 S Oak St + 1905 W 2nd Ave, Spokane WA 99201 |
| tc34 | fund-iv | 20 | 735 | 7.0% | 3401 Pacific Ave, Tacoma WA 98418 |
| tc68 | fund-iv | 176 | 1993 | 7.0% | 6830 Tacoma Mall Blvd, Tacoma WA 98409 |
| v202 | fund-iv | 15 | 719 | 7.0% | 12002 E Valleyway Ave, Spokane Valley WA 99206 |
| p705 | fund-0 | 4 | 40 | 8.0% | 705 S Perry St, Spokane WA 99202 |

## Steps

### Step 1: Determine date range

Compute the trailing 12-month window:
- `end_month`: the last full month. If a date is given, use that month. Otherwise, use the prior completed month (e.g. if today is May 31, end_month = April 2026).
- `start_month`: 12 months before end_month (e.g. May 2025).
- `prior_year_end`: end_month minus 12 months (e.g. Apr 2025). Used for YoY.
- `prior_year_start`: start_month minus 12 months (e.g. May 2024). Used for Historical T-12.

### Step 2: Fetch data from AppFolio

Make THREE AppFolio API calls:

**Call 1: Current T-12 (monthly breakdown)**
```
mcp__appfolio-mcp__get_income_statement_12_month_report
  posted_on_from: "<start_month as YYYY-MM>"
  posted_on_to: "<end_month as YYYY-MM>"
  properties: {"properties_ids": ["<id1>", "<id2>", ...]}
  level_of_detail: "detail_view"
```

**Call 2: Prior year same month (for YoY comparison)**
```
mcp__appfolio-mcp__get_income_statement_date_range_report
  posted_on_from: "<prior_year_end first day, YYYY-MM-DD>"
  posted_on_to: "<prior_year_end last day, YYYY-MM-DD>"
  properties: {"properties_ids": ["<id1>", ...]}
  level_of_detail: "detail_view"
```

**Call 3: Prior T-12 total (for Historical T-12 NOI YoY)**
```
mcp__appfolio-mcp__get_income_statement_date_range_report
  posted_on_from: "<prior_year_start first day, YYYY-MM-DD>"
  posted_on_to: "<prior_year_end last day, YYYY-MM-DD>"
  properties: {"properties_ids": ["<id1>", ...]}
  level_of_detail: "detail_view"
```

For properties with multiple IDs (kn47, o155), pass all IDs in the array.

### Step 3: Build the report

Use the generator script at `~/vault/m5x2/reports/2026.q1/generate_pnl_reports.py` as the reference for GL account mapping and formatting logic. The key mappings:

**Income grouping:**
- "Rent Income" = GL 40001 + 40002 + 40104 + 40110 + 40120 + 40130 + 41140 + 40240
- "Utility Reimb" = 40210
- "Late Fees" = 40260
- "Laundry" = 41101
- "Pet Rent/Fee" = 41112 + 41122 + 41142 + 41152
- "Parking" = 41113 + 41123 + 41133
- "Move-In/Out" = 41201 + 41202
- "Concessions" = 41150
- "Other Income" = 40250 + 41104 + 41203 + 41301 + 41302 + 41304 + 41403 + 40107 + 40310 + anything else income

**OpEx grouping:**
- "Prop Mgmt" = 50001 + 50002 + 50100 + 50601
- "Pest Control" = 53005
- "Insurance" = 53002
- "Prop Taxes" = 53003
- "R&M Repairs" = 52001 + 52004
- "R&M Turns" = 52002
- "R&M Grounds" = 52003
- "Electric/Gas" = 51001
- "Water" = 51002
- "Garbage" = 51003 + 51004

**Below-NOI:**
- "Mortgage Interest" = 80310
- "Mortgage Principal" = 80210
- "Legal" = 80410
- "CapEx Turns" = 80121
- "CapEx Appliances" = 80122
- "CapEx Disc" = 80130
- "CapEx Non-disc" = 80140

**Derived Metrics (per month):**
- NOI = Total Income - Total OpEx
- Cashflow = NOI - Total Deductions
- T-3 Ann NOI = (avg of trailing 3 months NOI) x 12
- T-12 NOI = rolling 12-month sum (need prior year months for intermediate values)
- Implied Value = T-12 NOI / cap_rate
- DSCR = NOI / (Interest + Principal)
- NOI/Unit/Mo = NOI / units

**Historical T-12 NOI table:**
Show monthly rolling T-12 from at least prior_year_end through end_month. Use Call 3 data to compute the prior-year T-12 baseline, then add/subtract monthly NOI to roll forward. Format as vertical table (one row per month).

**Comparisons section:**
| Metric | end_month | end_month-1 | Δ MoM | prior_year_end | Δ YoY |
Use summary metrics: Total Income, Total OpEx, NOI, Cashflow, T-12 NOI, Implied Value, DSCR, NOI/Unit/Mo.
Follow with 1-2 sentence narrative on key drivers.

**Commentary:**
2-3 bullets on NOI trend, R&M pattern, notable items.

### Step 4: Validate

After generating, run these checks:

1. **Total Income cross-check**: For each month, sum all income line items. Compare to AppFolio's "Total Income" row. Flag if delta > $1.
2. **Total Expense cross-check**: Sum OpEx + Below-NOI line items. Compare to AppFolio's "Total Expense" row. Flag if delta > $1.
3. **NOI sanity**: T-12 NOI should be positive for all active properties. Flag if negative.
4. **R&M concentration**: Flag if any single month's R&M (Repairs + Turns + Grounds) exceeds 30% of that month's Total Income.
5. **Vacancy rate**: If Vacancy (GL 40120) / GPR (GL 40110) > 15% for T-12, note in commentary.

If any check fails, append a `> **Validation Warning**` block to the report.

### Step 5: Write file

Output to: `~/vault/m5x2/reports/2026.q1/<fund>/<date>-<code>-trailing-12m-pnl.md`

Where `<date>` is today's date in `YYYY.MM.DD` format.

### Step 6: Report

```
pnl → <code> (<fund>, <units> units)
  T-12 NOI: $X | Implied Value: $XK | DSCR: X.XX
  File: <path>
  Validation: <pass|N warnings>
```

## Formatting Rules

- Round to nearest dollar in tables
- Use commas for thousands (1,234)
- Negative values in parentheses in the main table: (1,234)
- Delta columns use +/- prefix: +1,234 (+5.6%)
- Use "(n/m)" when dividing by zero or sign-flipping base
- Implied Value shown as NK (e.g. 894K)
- Skip rows that are all zeros across all months
- Cap rate from property registry (8.0% for Fund 0/I/II, 7.0% for Fund III/IV)
- No em-dashes in text
- Footer: "Generated from AppFolio on YYYY.MM.DD."

## Response Style

Execute silently. Show the Step 6 summary line. If validation warnings, list them.
