#!/usr/bin/env python3
"""Generate a trailing 12-month P&L report for an m5x2 property.

Fetches data directly from AppFolio's API (no MCP intermediary),
applies GL account mapping, and writes a markdown report.

Usage:
    python3 pnl-fast.py <property_code> [--date YYYY-MM] [--dry-run]

Examples:
    python3 pnl-fast.py s300
    python3 pnl-fast.py b101 --date 2026-04
    python3 pnl-fast.py s300 --dry-run   # print to stdout, don't write file
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path

# ---------------------------------------------------------------------------
# AppFolio API config
# ---------------------------------------------------------------------------

VHOST = "mckay"
API_USER = "74a8e81d16c91110e9985fedf53d7f24"
API_PASS = "4920d4f8c58a8e9fbcaf22268ec0689a"
BASE_URL = f"https://{VHOST}.appfolio.com/api/v2/reports"

VAULT = Path.home() / "vault"
REPORTS_BASE = VAULT / "m5x2" / "reports"

# ---------------------------------------------------------------------------
# Property registry
# ---------------------------------------------------------------------------

# Cap rates from SREO: q1 sreo tab, col L, Google Sheet 1noFafK85LLhd4Umzh84XVOqT3tR42Om0Wf4jBc3ShSU
PROPERTIES = {
    "a916": {"fund": "fund-0", "units": 12, "ids": ["2"], "cap": 0.080, "addr": "916 W Augusta Ave, Spokane WA 99205"},
    "a210": {"fund": "fund-0", "units": 10, "ids": ["39"], "cap": 0.078, "addr": "4210 N Avalon Rd, Spokane Valley WA 99216"},
    "h604": {"fund": "fund-0", "units": 1, "ids": ["36"], "cap": None, "addr": "604 E Hartson Ave, Spokane WA 99202"},
    "m608": {"fund": "fund-0", "units": 9, "ids": ["35"], "cap": 0.075, "addr": "1608 W Main Ave, Spokane WA 99201"},
    "p705": {"fund": "fund-0", "units": 20, "ids": ["47"], "cap": 0.075, "addr": "2705 N Pines Rd, Spokane Valley WA 99206"},
    "s300": {"fund": "fund-i", "units": 14, "ids": ["8"], "cap": 0.083, "addr": "9300 E Sprague Ave, Spokane Valley WA 99206"},
    "b101": {"fund": "fund-i", "units": 17, "ids": ["42"], "cap": 0.080, "addr": "1010 W Boone Ave, Spokane WA 99201"},
    "m405": {"fund": "fund-i", "units": 26, "ids": ["50"], "cap": 0.085, "addr": "405 S Maple St, Spokane WA 99201"},
    "h731": {"fund": "fund-i", "units": 8, "ids": ["228"], "cap": 0.070, "addr": "731 S Hatch St, Spokane WA 99202"},
    "hl73": {"fund": "fund-i", "units": 6, "ids": ["49"], "cap": 0.080, "addr": "730 N Jackson St, Helena MT 59601"},
    "k104": {"fund": "fund-i", "units": 59, "ids": ["220"], "cap": 0.070, "addr": "314 N LeFevre St, Medical Lake WA 99022"},
    "m221": {"fund": "fund-i", "units": 14, "ids": ["223"], "cap": 0.080, "addr": "1221 N Monroe St, Spokane WA 99201"},
    "ms22": {"fund": "fund-i", "units": 8, "ids": ["46"], "cap": 0.080, "addr": "2252 W Central Ave, Missoula MT 59801"},
    "ms43": {"fund": "fund-i", "units": 15, "ids": ["43"], "cap": 0.070, "addr": "430 Washington, Missoula MT 59802"},
    "ps17": {"fund": "fund-i", "units": 18, "ids": ["222"], "cap": 0.078, "addr": "1740 N 5th Ave, Pasco WA 99301"},
    "ps25": {"fund": "fund-i", "units": 44, "ids": ["221"], "cap": 0.070, "addr": "2524 W Sylvester St, Pasco WA 99301"},
    "ps91": {"fund": "fund-i", "units": 14, "ids": ["227"], "cap": 0.078, "addr": "908 W Ruby, Pasco WA 99301"},
    "w117": {"fund": "fund-ii", "units": 10, "ids": ["225"], "cap": 0.080, "addr": "1117 W 5th Ave, Spokane WA 99204"},
    "w226": {"fund": "fund-ii", "units": 10, "ids": ["224"], "cap": 0.075, "addr": "1226 W 5th Ave, Spokane WA 99204"},
    "hl65": {"fund": "fund-ii", "units": 13, "ids": ["44"], "cap": 0.085, "addr": "645 N Ewing, Helena MT 59601"},
    "c313": {"fund": "fund-iii", "units": 45, "ids": ["603"], "cap": 0.070, "addr": "3131 S Cook St, Spokane WA 99223"},
    "e328": {"fund": "fund-iii", "units": 7, "ids": ["490"], "cap": 0.070, "addr": "3128 E 28th Ave, Spokane WA 99223"},
    "j312": {"fund": "fund-iii", "units": 20, "ids": ["521"], "cap": 0.070, "addr": "3102 E Jackson Ave, Spokane WA 99207"},
    "k308": {"fund": "fund-iii", "units": 8, "ids": ["617"], "cap": 0.070, "addr": "308 N Washington St, Medical Lake WA 99022"},
    "l912": {"fund": "fund-iii", "units": 10, "ids": ["533"], "cap": 0.065, "addr": "912 W Lincoln Pl, Spokane WA 99204"},
    "rl16": {"fund": "fund-iii", "units": 100, "ids": ["414"], "cap": 0.070, "addr": "1621 George Washington Way, Richland WA 99354"},
    "rl21": {"fund": "fund-iii", "units": 14, "ids": ["648"], "cap": 0.085, "addr": "2153 Stevens Dr, Richland WA 99354"},
    "s129": {"fund": "fund-iii", "units": 20, "ids": ["461"], "cap": 0.070, "addr": "12903 E Sprague Ave, Spokane Valley WA 99216"},
    "w225": {"fund": "fund-iii", "units": 25, "ids": ["415"], "cap": 0.080, "addr": "225 S Wall St, Spokane WA 99201"},
    "a511": {"fund": "fund-iv", "units": 10, "ids": ["676"], "cap": 0.070, "addr": "511 E Augusta Ave, Spokane WA 99207"},
    "kn47": {"fund": "fund-iv", "units": 18, "ids": ["1057", "1121", "1130"], "cap": 0.061, "addr": "4711 W Metaline + 632 N Arthur, Kennewick WA 99336"},
    "o155": {"fund": "fund-iv", "units": 40, "ids": ["1224", "1183"], "cap": 0.070, "addr": "155 S Oak + 1905 W 2nd, Spokane WA 99201"},
    "tc34": {"fund": "fund-iv", "units": 20, "ids": ["735"], "cap": 0.068, "addr": "3401 Pacific Ave, Tacoma WA 98418"},
    "tc68": {"fund": "fund-iv", "units": 176, "ids": ["1993"], "cap": 0.068, "addr": "6830 Tacoma Mall Blvd, Tacoma WA 98409"},
    "v202": {"fund": "fund-iv", "units": 15, "ids": ["719"], "cap": 0.065, "addr": "12002 E Valleyway Ave, Spokane Valley WA 99206"},
}

# SREO Values from q1 sreo tab, column E (in thousands)
SREO_VALUES = {
    "a210": 1111, "a511": 974, "a916": 1100, "b101": 1450, "c313": 5570,
    "e328": 825, "h731": 1150, "hl65": 1250, "hl73": 591, "j312": 2174,
    "k104": 8000, "k308": 1220, "kn47": 25400, "l912": 2188, "m221": 1800,
    "m405": 2000, "m608": 1123, "ms22": 875, "ms43": 1850, "o155": 4300,
    "p705": 2700, "ps17": 1600, "ps25": 6300, "ps91": 1650, "rl16": 14000,
    "rl21": 1600, "s129": 2574, "s300": 1350, "tc34": 3300, "tc68": 15800,
    "v202": 2600, "w117": 800, "w225": 900, "w226": 1345,
}

# Value one year ago from q1 sreo tab, column C ("Value Y-1", in thousands).
# Used as the prior-year value baseline for change-in-value and the ROE/RORE
# denominators. Properties absent here (the newest acquisitions, blank in col C)
# show ROE/RORE as unknown. Refresh alongside SREO_VALUES when the sheet updates.
SREO_VALUES_1YR_AGO = {
    "a210": 1013, "a916": 1413, "b101": 1638, "c313": 5540, "e328": 825,
    "h604": 295, "h731": 1202, "hl65": 1213, "hl73": 645, "j312": 2174,
    "k104": 9140, "k308": 1220, "l912": 2188, "m221": 1780, "m405": 2572,
    "m608": 1239, "ms22": 900, "ms43": 1652, "p705": 3000, "ps17": 1800,
    "ps25": 6525, "ps91": 1579, "rl16": 15295, "rl21": 1850, "s129": 2630,
    "s300": 1374, "tc34": 3300, "w117": 840, "w225": 800, "w226": 1316,
}

# Outstanding mortgage balances (whole dollars) from the "M5x2 Outstanding
# Mortgages" sheet (1MSQ9wuA2WgMjiE4FrSVJ4v37ncp1ed_J3JKSXe-GzJU), as of
# DEBT_AS_OF. Per-property total includes construction loans / LOC where the
# property carries more than one note (m608 + construction, k104 + LOC).
# Equity = SREO Value - debt. Properties absent here carry no listed debt
# (e.g. w225), so equity can't be computed and is shown as unknown.
DEBT_AS_OF = "May 31, 2026"
DEBT_BALANCES = {
    "a916": 545_924, "m608": 472_141 + 249_700, "h604": 115_354,
    "a210": 596_017, "p705": 1_948_147,
    "s300": 738_175, "ms43": 1_136_467, "b101": 915_438, "ms22": 462_967,
    "hl73": 340_744, "k104": 4_140_674 + 980_914, "m405": 1_248_579,
    "h731": 844_503, "m221": 1_338_033, "ps17": 1_123_983, "ps25": 4_616_551,
    "ps91": 927_998,
    "w226": 678_273, "w117": 679_916, "hl65": 620_103,
    "c313": 3_946_978, "e328": 660_000, "j312": 1_674_000, "k308": 917_095,
    "l912": 895_060, "rl16": 9_919_198, "rl21": 1_209_723, "s129": 1_892_909,
    "a511": 716_894, "kn47": 19_050_000, "tc34": 2_475_000, "v202": 2_160_000,
    "o155": 3_225_353, "tc68": 11_887_500,
}

# ---------------------------------------------------------------------------
# GL Account Mapping
# ---------------------------------------------------------------------------

RENT_INCOME_ACCOUNTS = {"40001", "40002", "40104", "40110", "40120", "40130", "41140", "40240"}
OTHER_INCOME_MAP = {
    "Utility Reimb": {"40210"},
    "Late Fees": {"40260"},
    "Laundry": {"41101"},
    "Pet Rent/Fee": {"41112", "41122", "41142", "41152"},
    "Parking": {"41113", "41123", "41133"},
    "Move-In/Out": {"41201", "41202"},
    "Concessions": {"41150"},
}
OTHER_INCOME_CATCHALL_CODES = {"40250", "41104", "41203", "41301", "41302", "41304", "41403", "40107", "40310"}

OPEX_MAP = {
    "Prop Mgmt": {"50001", "50002", "50100", "50601"},
    "Pest Control": {"53005"},
    "Insurance": {"53002"},
    "Prop Taxes": {"53003"},
    "R&M Repairs": {"52001", "52004"},
    "R&M Turns": {"52002"},
    "R&M Grounds": {"52003"},
    "Electric/Gas": {"51001"},
    "Water": {"51002"},
    "Garbage": {"51003", "51004"},
}
OPEX_OTHER_CODES = {"53001", "50800", "50003"}  # 50003 = Lease Bonus Fee

BELOW_NOI_MAP = {
    "Mortgage Interest": {"80310"},
    "Mortgage Principal": {"80210"},
    "Legal": {"80410"},
    "CapEx Turns": {"80121"},
    "CapEx Appliances": {"80122"},
    "CapEx Disc": {"80130"},
    "CapEx Non-disc": {"80140"},
}

INCOME_ROWS = ["Rent Income", "Utility Reimb", "Late Fees", "Laundry",
               "Pet Rent/Fee", "Parking", "Move-In/Out", "Concessions", "Other Income"]
OPEX_ROWS = ["Prop Mgmt", "Pest Control", "Insurance", "Prop Taxes",
             "R&M Repairs", "R&M Turns", "R&M Grounds", "Electric/Gas",
             "Water", "Garbage", "Other OpEx"]
BELOW_NOI_ROWS = ["Mortgage Interest", "Mortgage Principal", "Legal",
                  "CapEx Turns", "CapEx Appliances", "CapEx Disc", "CapEx Non-disc"]


def build_account_lookup():
    lookup = {}
    for code in RENT_INCOME_ACCOUNTS:
        lookup[code] = ("income", "Rent Income")
    for label, codes in OTHER_INCOME_MAP.items():
        for code in codes:
            lookup[code] = ("income", label)
    for code in OTHER_INCOME_CATCHALL_CODES:
        lookup[code] = ("income", "Other Income")
    for label, codes in OPEX_MAP.items():
        for code in codes:
            lookup[code] = ("opex", label)
    for code in OPEX_OTHER_CODES:
        lookup[code] = ("opex", "Other OpEx")
    for label, codes in BELOW_NOI_MAP.items():
        for code in codes:
            lookup[code] = ("below_noi", label)
    return lookup


ACCOUNT_LOOKUP = build_account_lookup()


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _auth_header():
    creds = f"{API_USER}:{API_PASS}"
    b64 = base64.b64encode(creds.encode()).decode()
    return f"Basic {b64}"


def appfolio_post(endpoint, payload, max_retries=4):
    """POST to AppFolio API, return parsed JSON.

    Retries on 429 (rate limit) and 5xx with exponential backoff. Batching
    several /pnl runs hammers the API and trips the limiter; without retry the
    budget/occupancy fetches would silently drop and the report would degrade
    (missing budget columns) even though the data exists.
    """
    url = f"{BASE_URL}/{endpoint}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": _auth_header(),
        },
        method="POST",
    )
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                backoff = 2 ** attempt  # 1s, 2s, 4s, 8s
                print(f"  {endpoint}: {e.code}, retrying in {backoff}s "
                      f"(attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                time.sleep(backoff)
                continue
            raise


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

# Accrual basis: books expenses when incurred, not when paid. The default
# (cash) basis leaves the most recent closed month income-only until vendor
# payments clear, which forced the comparisons to anchor a month back. Accrual
# posts a closed month's expenses immediately and keeps all 12 months on one
# consistent method, so NOI/comparisons reflect the latest closed month.
ACCOUNTING_BASIS = "Accrual"


def fetch_12m_income_statement(prop_ids, from_month, to_month):
    """Fetch the 12-month income statement. Returns raw API rows."""
    payload = {
        "posted_on_from": from_month,
        "posted_on_to": to_month,
        "property_visibility": "active",
        "fund_type": "all",
        "level_of_detail": "detail_view",
        "include_zero_balance_gl_accounts": "0",
        "accounting_basis": ACCOUNTING_BASIS,
        "properties": {"properties_ids": prop_ids},
    }
    return appfolio_post("twelve_month_income_statement.json", payload)


def fetch_prior_year_12m(prop_ids, from_month, to_month):
    """Fetch prior-year 12-month statement for historical table and YoY."""
    payload = {
        "posted_on_from": from_month,
        "posted_on_to": to_month,
        "property_visibility": "active",
        "fund_type": "all",
        "level_of_detail": "detail_view",
        "include_zero_balance_gl_accounts": "0",
        "accounting_basis": ACCOUNTING_BASIS,
        "properties": {"properties_ids": prop_ids},
    }
    return appfolio_post("twelve_month_income_statement.json", payload)


def fetch_budget_comparison(prop_ids, period_from, period_to):
    """Fetch budget vs actual comparison for a period (YYYY-MM). Returns raw rows."""
    payload = {
        "period_from": period_from,
        "period_to": period_to,
        "comparison_period_from": period_from,
        "comparison_period_to": period_to,
        "properties": {"properties_ids": prop_ids},
        "property_visibility": "active",
        "accounting_basis": ACCOUNTING_BASIS,
    }
    return appfolio_post("budget_comparison.json", payload)


def fetch_lease_history(prop_ids, from_year, to_year):
    """Fetch the full occupancy history (incl. moved-out tenants) for lease
    activity flows. Each record carries move_in, lease_expires, last_lease_renewal
    and move_out dates, which we bucket into Acquired / Expired / Renewed per month.
    exclude_occupancies_with_move_out=0 is what makes past tenants visible.

    The ends_on window filters on lease EXPIRY, so it's padded ±2 years beyond
    the target years: a lease renewed (or moved into) in a target year can have
    a term that ends outside it (e.g. renewed in 2026, expires 2027), and a too-
    narrow window would drop those activity events."""
    payload = {
        "ends_on_from": f"{from_year - 2}-01",
        "ends_on_to": f"{to_year + 2}-12",
        "properties": {"properties_ids": prop_ids},
        "exclude_month_to_month": "0",
        "exclude_occupancies_with_move_out": "0",
    }
    return appfolio_post("lease_expiration_detail.json", payload)


def fetch_unit_snapshot(prop_ids, as_of_date):
    """Fetch a per-unit rent roll snapshot for the current term/MTM/vacant
    reconciliation. One row per unit, so it sums to the unit count exactly."""
    payload = {
        "as_of_date": as_of_date,
        "properties": {"properties_ids": prop_ids},
    }
    return appfolio_post("rent_roll_itemized.json", payload)


def fetch_occupancy(prop_ids, as_of_date):
    """Fetch occupancy summary for a property as of a specific date.

    Returns (occupied, total_units) tuple.
    """
    payload = {
        "as_of_to": as_of_date,
        "properties": {"properties_ids": prop_ids},
        "unit_visibility": "active",
    }
    data = appfolio_post("occupancy_summary.json", payload)
    results = data.get("results", [])
    total = sum(r.get("number_of_units", 0) for r in results)
    occupied = sum(r.get("occupied", 0) for r in results)
    return occupied, total


def fetch_occupancy_trio(prop_ids, latest_month, prior_month, yoy_month):
    """Fetch occupancy for 3 month-ends: latest, prior (MoM), YoY.

    Returns dict of {month_key: (occupied, total)} or None on failure.
    """
    import calendar

    def month_end(ym):
        y, m = int(ym[:4]), int(ym[5:7])
        _, last_day = calendar.monthrange(y, m)
        return f"{y:04d}-{m:02d}-{last_day:02d}"

    occ = {}
    for mk in [latest_month, prior_month, yoy_month]:
        if mk is None:
            continue
        try:
            occupied, total = fetch_occupancy(prop_ids, month_end(mk))
            occ[mk] = (occupied, total)
        except Exception:
            pass
    return occ


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def compute_months(end_month_str):
    """Given end month 'YYYY-MM', return list of 12 months and labels."""
    y, m = int(end_month_str[:4]), int(end_month_str[5:7])
    months = []
    for i in range(11, -1, -1):
        mm = m - i
        yy = y
        while mm <= 0:
            mm += 12
            yy -= 1
        months.append(f"{yy:04d}-{mm:02d}")
    # Build labels
    month_abbr = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    labels = []
    for j, mk in enumerate(months):
        yy, mm = int(mk[:4]), int(mk[5:7])
        abbr = month_abbr[mm - 1]
        short_yr = str(yy)[2:]
        if j == 0 or mm == 1:
            labels.append(f"{abbr} {short_yr}")
        else:
            labels.append(abbr)
    return months, labels


def parse_api_rows(rows, months):
    """Parse API response into monthly[month][label] and appfolio_totals."""
    monthly = defaultdict(lambda: defaultdict(float))
    af_totals = defaultdict(lambda: defaultdict(float))
    # Also track raw GL lines for comparisons
    gl_lines = defaultdict(lambda: defaultdict(float))  # gl_lines[month][(code, name)]
    # Track GPR (GL 40110) separately for ancillary income % calculation
    gpr_monthly = defaultdict(float)

    for row in rows:
        code = row.get("account_code")
        name = row.get("account_name", "")
        month_data = row.get("months", [])

        for md in month_data:
            mk = md.get("id", "")
            if mk not in months:
                continue
            try:
                val = float(md.get("value", "0"))
            except (ValueError, TypeError):
                continue

            if code is None or code == "":
                name_lower = name.strip().lower()
                if "total income" in name_lower:
                    af_totals[mk]["Total Income"] = val
                elif "total expense" in name_lower:
                    af_totals[mk]["Total Expense"] = val
                continue

            code_str = str(code).strip()
            gl_lines[mk][(code_str, name)] = val

            # Track GPR separately
            if code_str == "40110":
                gpr_monthly[mk] += val

            if code_str in ACCOUNT_LOOKUP:
                _, label = ACCOUNT_LOOKUP[code_str]
                monthly[mk][label] += val

    return dict(monthly), dict(af_totals), dict(gl_lines), dict(gpr_monthly)


def make_budget_struct(rows):
    """Parse budget_comparison rows into a struct keyed by the same P&L labels.

    Each per-line label maps to (budget, actual, favorable_variance) via the
    shared ACCOUNT_LOOKUP. AppFolio's own variance is favorable-signed
    (positive = better than budget: more income or less expense), and we keep
    that convention. Total rows (account_number is null) provide operating
    income/expense and total expense, from which NOI / Deductions / Cashflow
    budgets are derived.

    Returns dict: {"lb": {label: budget}, "la": {label: actual},
    "lv": {label: favorable_variance}, "<Total label>": (actual, budget, var)}
    or None if rows is falsy.
    """
    if not rows:
        return None

    lb = defaultdict(float)  # budget by label
    la = defaultdict(float)  # actual by label
    lv = defaultdict(float)  # favorable variance by label
    tot = {}

    for row in rows:
        code = row.get("account_number")
        name = (row.get("account_name") or "").strip().lower()
        try:
            a = float(row.get("total_period_actual") or 0)
            b = float(row.get("total_period_budget") or 0)
            v = float(row.get("total_period_variance") or 0)
        except (TypeError, ValueError):
            continue

        if code is None or code == "":
            if "operating income" in name:
                tot["op_inc"] = (a, b, v)
            elif "operating expense" in name:
                tot["op_exp"] = (a, b, v)
            elif "expense" in name:
                tot["tot_exp"] = (a, b, v)
            # "Total Budgeted Income" (== operating income) is ignored
            continue

        cs = str(code).strip()
        if cs in ACCOUNT_LOOKUP:
            _, label = ACCOUNT_LOOKUP[cs]
            lb[label] += b
            la[label] += a
            lv[label] += v

    op_inc = tot.get("op_inc", (0.0, 0.0, 0.0))
    op_exp = tot.get("op_exp", (0.0, 0.0, 0.0))
    tx = tot.get("tot_exp", (0.0, 0.0, 0.0))
    # NOI: higher is better -> favorable var = income_var + expense_var
    noi = (op_inc[0] - op_exp[0], op_inc[1] - op_exp[1], op_inc[2] + op_exp[2])
    # Below-NOI deductions: expense -> favorable var = total_exp_var - op_exp_var
    ded = (tx[0] - op_exp[0], tx[1] - op_exp[1], tx[2] - op_exp[2])
    cf = (noi[0] - ded[0], noi[1] - ded[1], noi[2] + ded[2])

    return {
        "lb": dict(lb), "la": dict(la), "lv": dict(lv),
        "Total Income": op_inc, "Total OpEx": op_exp,
        "NOI": noi, "Total Deductions": ded, "Cashflow": cf,
    }


def find_anchor_idx(months, af_totals):
    """Return the index of the latest month with BOTH income and expense posted.

    Booking lag means the most recent month often has income but zero expense
    (expenses not yet posted). The comparisons anchor on the latest *complete*
    month. Falls back to the last month if none qualify.
    """
    for i in range(len(months) - 1, -1, -1):
        af = af_totals.get(months[i], {})
        inc = af.get("Total Income", 0)
        exp = af.get("Total Expense", 0)
        if abs(inc) > 0.005 and abs(exp) > 0.005:
            return i
    return len(months) - 1


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------

def compute_summaries(monthly, months, units):
    summaries = {}
    for month in months:
        m = monthly.get(month, {})
        total_income = sum(m.get(r, 0) for r in INCOME_ROWS)
        total_opex = sum(m.get(r, 0) for r in OPEX_ROWS)
        noi = total_income - total_opex
        total_ded = sum(m.get(r, 0) for r in BELOW_NOI_ROWS)
        cashflow = noi - total_ded
        mortgage = m.get("Mortgage Interest", 0) + m.get("Mortgage Principal", 0)
        dscr = noi / mortgage if mortgage != 0 else None
        noi_unit = noi / units if units > 0 else 0
        summaries[month] = {
            "Total Income": total_income,
            "Total OpEx": total_opex,
            "NOI": noi,
            "Total Deductions": total_ded,
            "Cashflow": cashflow,
            "Mortgage": mortgage,
            "DSCR": dscr,
            "NOI/Unit/Mo": noi_unit,
        }
    return summaries


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt(val, bold=False):
    if val is None:
        return "\u2014"
    r = round(val)
    s = f"({abs(r):,})" if r < 0 else f"{r:,}"
    return f"**{s}**" if bold else s


def fmt_k(val):
    """Format large values as K (e.g. 1,219K)."""
    if val is None:
        return "\u2014"
    r = round(val / 1000)
    return f"{r:,}K"


def fmt_signed(val):
    """Signed integer with thousands separators, no percentage. None -> em-dash."""
    if val is None:
        return "\u2014"
    r = round(val)
    sign = "+" if r > 0 else ""
    return f"{sign}{r:,}"


def fmt_delta(cur, prev):
    """Dollar delta only (percentages removed per report spec)."""
    if cur is None or prev is None:
        return "\u2014"
    return fmt_signed(cur - prev)


# ---------------------------------------------------------------------------
# Report building
# ---------------------------------------------------------------------------

def build_pnl_table(monthly, summaries, months, labels):
    hdr = "| Account |" + "".join(f" {l} |" for l in labels) + " **T12** |"
    sep = "|:--------|" + " -------:|" * len(labels) + " -------:|"
    lines = [hdr, sep]

    def add_row(label, vals, bold=False, force=False):
        if not force and all(vals.get(m, 0) == 0 for m in months):
            return
        row = f"| {'**' + label + '**' if bold else label} |"
        t12 = 0
        for m in months:
            v = vals.get(m, 0)
            t12 += v
            row += f" {fmt(v, bold)} |"
        row += f" {fmt(t12, bold)} |"
        lines.append(row)

    def blank():
        lines.append("|" + " |" * (len(labels) + 2))

    # Income
    for label in INCOME_ROWS:
        add_row(label, {m: monthly.get(m, {}).get(label, 0) for m in months})
    add_row("Total Income", {m: summaries[m]["Total Income"] for m in months}, bold=True, force=True)
    blank()

    # OpEx
    for label in OPEX_ROWS:
        add_row(label, {m: monthly.get(m, {}).get(label, 0) for m in months})
    add_row("Total OpEx", {m: summaries[m]["Total OpEx"] for m in months}, bold=True, force=True)
    blank()

    # NOI
    add_row("NOI", {m: summaries[m]["NOI"] for m in months}, bold=True, force=True)
    blank()

    # Below-NOI
    for label in BELOW_NOI_ROWS:
        add_row(label, {m: monthly.get(m, {}).get(label, 0) for m in months})
    add_row("Total Deductions", {m: summaries[m]["Total Deductions"] for m in months}, bold=True, force=True)
    blank()

    # Cashflow
    add_row("Cashflow", {m: summaries[m]["Cashflow"] for m in months}, bold=True, force=True)

    return "\n".join(lines) + "\n"


def build_derived_metrics(summaries, months, labels, cap_rate, units, prior_monthly_noi=None, sreo_value=None):
    """Build derived metrics table. prior_monthly_noi is dict[month] -> NOI for prior year months."""
    lines = ["### Derived Metrics", ""]
    hdr = "| Metric |" + "".join(f" {l} |" for l in labels)
    sep = "|:-------|" + " -------:|" * len(labels)
    lines.extend([hdr, sep])
    cap_pct = f"{cap_rate*100:.1f}%" if cap_rate else "n/a"

    # T-3 Ann NOI
    row = "| T-3 Ann NOI |"
    for i, m in enumerate(months):
        if i < 2:
            # Try to use prior-year data to fill in
            if prior_monthly_noi and i == 0:
                # Need months i-2, i-1 from prior year
                # For first month, we'd need 2 months before our window
                row += " \u2014 |"
            else:
                row += " \u2014 |"
        else:
            three = [months[j] for j in range(i - 2, i + 1)]
            avg = sum(summaries[mo]["NOI"] for mo in three) / 3
            row += f" {fmt(avg * 12)} |"
    lines.append(row)

    # T-12 NOI (rolling, using prior-year data to extend back)
    t12_vals = {}
    row = "| T-12 NOI |"
    for i, m in enumerate(months):
        if prior_monthly_noi:
            # We can compute T-12 for every month by filling gaps with prior year
            noi_window = []
            for j in range(i - 11, i + 1):
                if 0 <= j < len(months):
                    noi_window.append(summaries[months[j]]["NOI"])
                elif prior_monthly_noi:
                    # Map to prior year month
                    target_offset = j  # negative index into prior year
                    pm_key = _prior_month_key(months[0], target_offset)
                    if pm_key and pm_key in prior_monthly_noi:
                        noi_window.append(prior_monthly_noi[pm_key])
            if len(noi_window) == 12:
                t12 = sum(noi_window)
                t12_vals[m] = t12
                row += f" {fmt(t12)} |"
            else:
                t12_vals[m] = None
                row += " \u2014 |"
        else:
            # No prior year data: annualize available months
            available_count = i + 1  # months 0..i
            noi_sum = sum(summaries[months[j]]["NOI"] for j in range(0, i + 1))
            t12 = noi_sum / available_count * 12
            t12_vals[m] = t12
            row += f" {fmt(t12)} |"
    lines.append(row)

    # Implied Value
    row = f"| Implied Value ({cap_pct}) |"
    for m in months:
        if t12_vals.get(m) is not None and cap_rate:
            row += f" {fmt_k(t12_vals[m] / cap_rate)} |"
        else:
            row += " \u2014 |"
    lines.append(row)

    # T-3 Ann Value
    row = f"| T-3 Ann Value ({cap_pct}) |"
    for i, m in enumerate(months):
        if i < 2 or not cap_rate:
            row += " \u2014 |"
        else:
            three = [months[j] for j in range(i - 2, i + 1)]
            avg = sum(summaries[mo]["NOI"] for mo in three) / 3
            row += f" {fmt_k(avg * 12 / cap_rate)} |"
    lines.append(row)

    # DSCR
    row = "| DSCR |"
    for m in months:
        d = summaries[m]["DSCR"]
        row += f" {d:.2f}x |" if d is not None else " \u2014 |"
    lines.append(row)

    # NOI/Unit/Mo
    row = "| NOI / Unit / Mo |"
    for m in months:
        row += f" {fmt(summaries[m]['NOI/Unit/Mo'])} |"
    lines.append(row)

    # SREO Value
    if sreo_value is not None:
        val_str = f"{sreo_value:,}K"
        row = "| SREO Value |"
        for m in months:
            row += f" {val_str} |"
        lines.append(row)
    else:
        row = "| SREO Value |"
        for m in months:
            row += " \u2014 |"
        lines.append(row)

    lines.append(f"T-3 Ann NOI = average of trailing 3 months NOI x 12.")
    lines.append(f"T-12 NOI = rolling 12-month sum ending at that month.")
    lines.append(f"Implied Value = T-12 NOI / {cap_pct}.")
    lines.append("")
    return "\n".join(lines) + "\n", t12_vals


def compute_equity_returns(sreo_value, sreo_value_1yr, debt, t12_cashflow, t12_principal):
    """Equity and trailing-12-month return metrics (ROE / RORE).

    sreo_value and sreo_value_1yr are in thousands; debt and flows in dollars.

    Total Return (the shared numerator) = T-12 cashflow + T-12 debt paydown
    + change in value, where change in value = current SREO value − value one
    year ago (q1 sreo col C).

    Prior-year equity is reconstructed from the balance sheet rather than rolled
    back through owner equity payments (AppFolio posts no owner contributions at
    the property level): equity 1yr ago = value_1yr − debt_1yr, with
    debt_1yr = debt_today + T-12 principal paid down. ROE divides Total Return by
    equity 1yr ago; RORE divides it by net realizable equity 1yr ago
    (equity 1yr ago − 9% sale cost on value_1yr). Ratios/numerator are None when
    an input is missing.
    """
    sreo = sreo_value * 1000 if sreo_value is not None else None
    sreo_1yr = sreo_value_1yr * 1000 if sreo_value_1yr is not None else None

    equity_today = (sreo - debt) if (sreo is not None and debt is not None) else None
    realizable_today = (equity_today - sreo * 0.09) if equity_today is not None else None

    debt_1yr = (debt + t12_principal) if debt is not None else None
    equity_1yr = (sreo_1yr - debt_1yr) if (sreo_1yr is not None and debt_1yr is not None) else None
    realizable_1yr = (equity_1yr - sreo_1yr * 0.09) if equity_1yr is not None else None

    change_in_value = (sreo - sreo_1yr) if (sreo is not None and sreo_1yr is not None) else None
    numerator = (t12_cashflow + t12_principal + change_in_value) if change_in_value is not None else None

    roe = (numerator / equity_1yr) if (numerator is not None and equity_1yr not in (None, 0)) else None
    rore = (numerator / realizable_1yr) if (numerator is not None and realizable_1yr not in (None, 0)) else None

    return {
        "equity_today": equity_today, "realizable_today": realizable_today,
        "equity_1yr": equity_1yr, "realizable_1yr": realizable_1yr,
        "debt_1yr": debt_1yr, "change_in_value": change_in_value,
        "t12_cashflow": t12_cashflow, "t12_principal": t12_principal,
        "numerator": numerator, "roe": roe, "rore": rore,
    }


def build_equity(code, sreo_value, sreo_value_1yr, debt, t12_cashflow, t12_principal):
    """Build the Equity section: Equity = SREO Value - outstanding debt, plus the
    trailing-12-month ROE and RORE.

    SREO value is stored in thousands; debt and flows in whole dollars. When an
    input is missing the dependent rows show an em-dash rather than a wrong
    number. See compute_equity_returns for the ROE/RORE definitions.
    """
    lines = ["## Equity", ""]
    lines.append("| Metric | Value |")
    lines.append("|:-------|------:|")

    er = compute_equity_returns(sreo_value, sreo_value_1yr, debt, t12_cashflow, t12_principal)
    sreo_dollars = sreo_value * 1000 if sreo_value is not None else None
    equity = er["equity_today"]
    sale_cost = sreo_dollars * 0.09 if sreo_dollars is not None else None
    realizable_equity = er["realizable_today"]

    def dollars(v):
        if v is None:
            return "—"
        return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"

    def pct(v):
        return f"{v * 100:.1f}%" if v is not None else "—"

    lines.append(f"| SREO Value | {dollars(sreo_dollars)} |")
    lines.append(f"| Outstanding Debt | {dollars(debt)} |")
    lines.append(f"| **Equity** | **{dollars(equity)}** |")
    lines.append(f"| Sale Cost (9%) | {dollars(sale_cost)} |")
    lines.append(f"| **Net Realizable Equity** | **{dollars(realizable_equity)}** |")

    if sreo_dollars and debt is not None and sreo_dollars != 0:
        ltv = debt / sreo_dollars * 100
        eq_pct = equity / sreo_dollars * 100
        lines.append(f"| LTV | {ltv:.1f}% |")
        lines.append(f"| Equity % | {eq_pct:.1f}% |")

    # Trailing-12-month return on equity
    lines.append(f"| T-12 Cashflow | {dollars(er['t12_cashflow'])} |")
    lines.append(f"| Debt Paydown | {dollars(er['t12_principal'])} |")
    lines.append(f"| Change in Value (YoY) | {dollars(er['change_in_value'])} |")
    lines.append(f"| **Total Return** | **{dollars(er['numerator'])}** |")
    lines.append(f"| Equity 1yr Ago | {dollars(er['equity_1yr'])} |")
    lines.append(f"| Realizable Equity 1yr Ago | {dollars(er['realizable_1yr'])} |")
    lines.append(f"| **ROE** | **{pct(er['roe'])}** |")
    lines.append(f"| **RORE** | **{pct(er['rore'])}** |")

    lines.append("")
    lines.append(
        "Equity = SREO Value − outstanding debt. SREO value from 2026 Q1 "
        "PFS + SREO (q1 sreo tab, col E). Debt = outstanding mortgage balance(s) "
        f"from M5x2 Outstanding Mortgages as of {DEBT_AS_OF} (includes "
        "construction loans / LOC where applicable). Net Realizable Equity = "
        "Equity − sale cost (9% of SREO Value), the net cash from a disposition."
    )
    lines.append("")
    lines.append(
        "Total Return = T-12 cashflow + T-12 debt paydown + change in value "
        "(current SREO value − value one year ago, q1 sreo col C). ROE = Total "
        "Return ÷ equity one year ago; RORE = Total Return ÷ realizable equity "
        "one year ago. Equity one year ago is reconstructed from the balance "
        "sheet: value_1yr − (debt_today + T-12 principal paid down), since "
        "AppFolio carries no property-level owner equity payments to roll back. "
        "ROE/RORE are blank when no prior-year value is on file for the property."
    )
    if debt is None:
        lines.append("")
        lines.append(f"_No debt on file for {code} in the mortgages sheet; equity not computed._")
    lines.append("")
    return "\n".join(lines) + "\n", equity


def _prior_month_key(first_month, offset):
    """Given first_month 'YYYY-MM' and negative offset, return the month key."""
    y, m = int(first_month[:4]), int(first_month[5:7])
    m += offset
    while m <= 0:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return f"{y:04d}-{m:02d}"



def build_ancillary_income(monthly, months, gpr_monthly):
    """Build the Ancillary Income section with latest month, T-3, and T-12."""
    ancillary_labels = ["Utility Reimb", "Laundry", "Pet Rent/Fee", "Parking",
                        "Late Fees", "Move-In/Out", "Other Income"]

    latest_month = months[-1]
    t3_months = months[-3:]

    t12_gpr = sum(gpr_monthly.get(m, 0) for m in months)
    t3_gpr = sum(gpr_monthly.get(m, 0) for m in t3_months)
    latest_gpr = gpr_monthly.get(latest_month, 0)

    # Build month label for latest
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    ly, lm = int(latest_month[:4]), int(latest_month[5:7])
    latest_label = f"{month_names[lm - 1]} {str(ly)[2:]}"

    lines = ["## Ancillary Income", ""]
    lines.append(f"| Metric | {latest_label} | T-3 Ann | T-12 | % of GPR |")
    lines.append("|--------|-----:|-----:|-----:|--------:|")

    total_latest = 0
    total_t3 = 0
    total_t12 = 0

    for label in ancillary_labels:
        latest_val = monthly.get(latest_month, {}).get(label, 0)
        t3_val = sum(monthly.get(m, {}).get(label, 0) for m in t3_months)
        t12_val = sum(monthly.get(m, {}).get(label, 0) for m in months)
        if t12_val == 0 and latest_val == 0:
            continue
        pct = (t12_val / t12_gpr * 100) if t12_gpr != 0 else 0
        t3_ann = t3_val * 4  # annualize the 3-month total
        total_latest += latest_val
        total_t3 += t3_val
        total_t12 += t12_val
        lines.append(f"| {label} | ${latest_val:,.0f} | ${t3_ann:,.0f} | ${t12_val:,.0f} | {pct:.1f}% |")

    pct_total = (total_t12 / t12_gpr * 100) if t12_gpr != 0 else 0
    t3_ann_total = total_t3 * 4
    lines.append(f"| **Total Ancillary** | **${total_latest:,.0f}** | **${t3_ann_total:,.0f}** | **${total_t12:,.0f}** | **{pct_total:.1f}%** |")

    # Arrears Billing = Utility Reimb / (Water + Electric/Gas + Garbage).
    # Garbage is in the denominator because tenants are billed back for it
    # (RUBS) just like water; omitting it inflates recovery above 100% at
    # properties where the owner pays little/no electric/gas (e.g. ms22).
    t12_util_reimb = sum(monthly.get(m, {}).get("Utility Reimb", 0) for m in months)
    t12_water = sum(monthly.get(m, {}).get("Water", 0) for m in months)
    t12_electric = sum(monthly.get(m, {}).get("Electric/Gas", 0) for m in months)
    t12_garbage = sum(monthly.get(m, {}).get("Garbage", 0) for m in months)
    util_cost = t12_water + t12_electric + t12_garbage
    arrears_pct = (t12_util_reimb / util_cost * 100) if util_cost != 0 else 0
    lines.append(f"| **Arrears Billing (Util Reimb / Water+Electric+Gas+Garbage)** | | | | **{arrears_pct:.1f}%** |")

    lines.append("")
    return "\n".join(lines) + "\n", total_t12, pct_total, arrears_pct


def build_lease_activity(history_data, snapshot_data, units, today):
    """Lease activity & exposure section.

    A current per-unit snapshot (term / MTM / vacant, reconciling to the unit
    count) followed by a monthly activity matrix for the prior and current
    calendar years:
      - Expired:  fixed lease term ends in the month (lease_expires)
      - Renewed:  lease renewal signed in the month (last_lease_renewal)
      - Acquired: new tenant moved in during the month (move_in)
    Prior year is fully known. Current year: expirations are scheduled for the
    full year, renewals are knowable ~a month ahead, acquisitions only through
    the current month; cells beyond what's knowable are left blank.
    """
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    lines = ["## Lease Activity & Exposure", ""]

    cy = today.year
    py = cy - 1
    renewed_known = min(today.month + 1, 12)   # renewals land ~a month ahead
    acquired_known = today.month               # move-ins are historical fact

    # --- Current snapshot (reconciles to unit count) ---
    snap = (snapshot_data or {}).get("results", []) if snapshot_data else []
    term = mtm = vacant = 0
    for u in snap:
        status = (u.get("status") or "")
        if status.startswith("Vacant"):
            vacant += 1
        elif u.get("lease_to"):
            term += 1
        else:
            mtm += 1
    counted = term + mtm + vacant
    if snap:
        # If the snapshot row count disagrees with the registry unit count, trust
        # the registry and fold the difference into vacant so it still reconciles.
        if counted != units:
            vacant += units - counted
        occupied = term + mtm
        lines.append(
            f"**Current ({units} units):** {occupied} occupied "
            f"({term} term · {mtm} MTM) · {max(vacant, 0)} vacant"
        )
        lines.append("")

    # --- Activity flows ---
    def bucket(field):
        out = defaultdict(int)
        for rec in (history_data or {}).get("results", []):
            d = rec.get(field)
            if d and len(d) >= 7:
                out[d[:7]] += 1
        return out

    if not history_data:
        lines.append("(activity data unavailable)")
        lines.append("")
        return "\n".join(lines) + "\n"

    acquired = bucket("move_in")
    expired = bucket("lease_expires")
    renewed = bucket("last_lease_renewal")

    header = "| Flow | " + " | ".join(month_names) + " | **Total** |"
    sep = "|:-----|" + "--:|" * 12 + "--:|"
    lines.append(header)
    lines.append(sep)

    def row(label, data, year, known_through):
        cells, total = [], 0
        for mo in range(1, 13):
            if mo > known_through:
                cells.append("")          # not yet knowable
                continue
            n = data.get(f"{year}-{mo:02d}", 0)
            total += n
            cells.append(str(n) if n else "·")
        return f"| {label} | " + " | ".join(cells) + f" | **{total}** |"

    lines.append(row(f"{py} Expired",  expired,  py, 12))
    lines.append(row(f"{py} Renewed",  renewed,  py, 12))
    lines.append(row(f"{py} Acquired", acquired, py, 12))
    lines.append(row(f"{cy} Expired",  expired,  cy, 12))
    lines.append(row(f"{cy} Renewed",  renewed,  cy, renewed_known))
    lines.append(row(f"{cy} Acquired", acquired, cy, acquired_known))

    lines.append("")
    lines.append(
        "Expired = fixed term ends · Renewed = renewal signed · "
        "Acquired = new move-in. "
        f"{cy} renewals known through {month_names[renewed_known - 1]}, "
        f"acquisitions through {month_names[acquired_known - 1]}; later cells blank."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def build_comparisons(monthly, summaries, months, labels, cap_rate, units, t12_vals,
                      prior_monthly=None, prior_summaries=None,
                      anchor_idx=None, budget_month=None, budget_ytd=None):
    """Build the comparisons section with full GL detail.

    Anchors on the latest *complete* month (anchor_idx), and adds five
    budget-based columns when budget data is available: month Budget,
    \u0394 Budget (favorable variance), Budget YTD, Actual YTD, and YTD Variance
    (all calendar-year-to-date through the anchor month). MoM/YoY deltas are
    dollar-only (no percentages).
    """
    lines = ["## Comparisons", ""]
    if anchor_idx is None:
        anchor_idx = len(months) - 1
    last = months[anchor_idx]
    prev = months[anchor_idx - 1]

    ly, lm = int(last[:4]), int(last[5:7])
    yoy_month = f"{ly - 1:04d}-{lm:02d}"

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    last_label = f"{month_names[lm - 1]} {str(ly)[2:]}"
    prev_y, prev_m_num = int(prev[:4]), int(prev[5:7])
    prev_label = f"{month_names[prev_m_num - 1]} {str(prev_y)[2:]}"
    yoy_label = f"{month_names[lm - 1]} {str(ly - 1)[2:]}"

    has_yoy = prior_summaries is not None and yoy_month in prior_summaries
    has_budget = budget_month is not None and budget_ytd is not None

    ytd_label = f"YTD {ly} (Jan\u2013{month_names[lm - 1]})"

    # Budget columns are always rendered for a consistent layout; when budget
    # data is unavailable the cells fall back to em-dashes (see budget_cells).
    # Header: actual | budget | \u0394bud | prev | \u0394MoM [| yoy | \u0394YoY] | budYTD | actYTD | YTD var
    hdr = f"| Account | {last_label} |"
    sep_line = "|:--------|-------:|"
    hdr += " Budget | \u0394 Bud |"
    sep_line += "-------:|------:|"
    hdr += f" {prev_label} | \u0394 MoM |"
    sep_line += "-------:|------:|"
    if has_yoy:
        hdr += f" {yoy_label} | \u0394 YoY |"
        sep_line += "-------:|------:|"
    hdr += f" Bud {ytd_label} | Act {ytd_label} | YTD Var |"
    sep_line += "-------:|-------:|------:|"
    lines.extend([hdr, sep_line])

    def budget_cells(label, is_budget_row):
        """Return (month_budget_str, month_var_str, ytd_bud_str, ytd_act_str, ytd_var_str)."""
        if not has_budget or not is_budget_row:
            return ("\u2014", "\u2014", "\u2014", "\u2014", "\u2014")
        bm = _budget_lookup(budget_month, label)
        by = _budget_lookup(budget_ytd, label)
        if bm is None or by is None:
            return ("\u2014", "\u2014", "\u2014", "\u2014", "\u2014")
        m_bud, _, m_var = bm
        y_bud, y_act, y_var = by
        return (fmt(m_bud), fmt_signed(m_var), fmt(y_bud), fmt(y_act), fmt_signed(y_var))

    def add_comp(label, cur, prv, yoy=None, bold=False, is_budget_row=True):
        lbl = f"**{label}**" if bold else label
        bc = budget_cells(label, is_budget_row)
        row = f"| {lbl} | {fmt(cur, bold)} |"
        row += f" {bc[0]} | {bc[1]} |"
        row += f" {fmt(prv, bold)} | {fmt_delta(cur, prv)} |"
        if has_yoy:
            yoy_fmt = fmt(yoy, bold) if yoy is not None else "\u2014"
            yoy_delta = fmt_delta(cur, yoy) if yoy is not None else "\u2014"
            row += f" {yoy_fmt} | {yoy_delta} |"
        row += f" {bc[2]} | {bc[3]} | {bc[4]} |"
        lines.append(row)

    last_m = monthly.get(last, {})
    prev_m = monthly.get(prev, {})
    yoy_m = prior_monthly.get(yoy_month, {}) if prior_monthly and has_yoy else {}

    # Income lines
    for label in INCOME_ROWS:
        c, p = last_m.get(label, 0), prev_m.get(label, 0)
        y_val = yoy_m.get(label, 0) if has_yoy else None
        if c == 0 and p == 0 and (y_val is None or y_val == 0):
            continue
        add_comp(label, c, p, y_val)

    c_ti = summaries[last]["Total Income"]
    p_ti = summaries[prev]["Total Income"]
    y_ti = prior_summaries[yoy_month]["Total Income"] if has_yoy else None
    add_comp("Total Income", c_ti, p_ti, y_ti, bold=True)

    # OpEx lines
    for label in OPEX_ROWS:
        c, p = last_m.get(label, 0), prev_m.get(label, 0)
        y_val = yoy_m.get(label, 0) if has_yoy else None
        if c == 0 and p == 0 and (y_val is None or y_val == 0):
            continue
        add_comp(label, c, p, y_val)

    c_oe = summaries[last]["Total OpEx"]
    p_oe = summaries[prev]["Total OpEx"]
    y_oe = prior_summaries[yoy_month]["Total OpEx"] if has_yoy else None
    add_comp("Total OpEx", c_oe, p_oe, y_oe, bold=True)

    c_noi = summaries[last]["NOI"]
    p_noi = summaries[prev]["NOI"]
    y_noi = prior_summaries[yoy_month]["NOI"] if has_yoy else None
    add_comp("NOI", c_noi, p_noi, y_noi, bold=True)

    # Below-NOI lines
    for label in BELOW_NOI_ROWS:
        c, p = last_m.get(label, 0), prev_m.get(label, 0)
        y_val = yoy_m.get(label, 0) if has_yoy else None
        if c == 0 and p == 0 and (y_val is None or y_val == 0):
            continue
        add_comp(label, c, p, y_val)

    c_td = summaries[last]["Total Deductions"]
    p_td = summaries[prev]["Total Deductions"]
    y_td = prior_summaries[yoy_month]["Total Deductions"] if has_yoy else None
    add_comp("Total Deductions", c_td, p_td, y_td, bold=True)

    c_cf = summaries[last]["Cashflow"]
    p_cf = summaries[prev]["Cashflow"]
    y_cf = prior_summaries[yoy_month]["Cashflow"] if has_yoy else None
    add_comp("Cashflow", c_cf, p_cf, y_cf, bold=True)

    # Derived metrics (no budget concept -> dashes in budget columns)
    t12_last = t12_vals.get(last)
    t12_prev = t12_vals.get(prev)
    add_comp("T-12 NOI", t12_last, t12_prev,
             t12_vals.get(yoy_month) if has_yoy else None, is_budget_row=False)

    if t12_last is not None and cap_rate:
        iv_last = t12_last / cap_rate
        iv_prev = t12_prev / cap_rate if t12_prev else None
        iv_last_s = fmt_k(iv_last)
        iv_prev_s = fmt_k(iv_prev) if iv_prev else "\u2014"
        if iv_prev is not None:
            iv_delta = fmt_signed(round(iv_last / 1000) - round(iv_prev / 1000)) + "K"
        else:
            iv_delta = "\u2014"
        bc = budget_cells("Implied Value", is_budget_row=False)
        row = f"| Implied Value | {iv_last_s} |"
        row += f" {bc[0]} | {bc[1]} |"
        row += f" {iv_prev_s} | {iv_delta} |"
        if has_yoy:
            yoy_t12 = t12_vals.get(yoy_month)
            if yoy_t12 is not None and yoy_t12 != 0:
                iv_yoy = yoy_t12 / cap_rate
                iv_yoy_delta = fmt_signed(round(iv_last / 1000) - round(iv_yoy / 1000)) + "K"
                row += f" {fmt_k(iv_yoy)} | {iv_yoy_delta} |"
            else:
                row += " \u2014 | \u2014 |"
        row += f" {bc[2]} | {bc[3]} | {bc[4]} |"
        lines.append(row)

    # DSCR (ratio, no budget concept)
    dscr_last = summaries[last]["DSCR"]
    dscr_prev = summaries[prev]["DSCR"]
    dscr_y = prior_summaries[yoy_month]["DSCR"] if has_yoy else None
    dl = f"{dscr_last:.2f}x" if dscr_last else "\u2014"
    dp = f"{dscr_prev:.2f}x" if dscr_prev else "\u2014"
    dy = f"{dscr_y:.2f}x" if dscr_y else "\u2014"
    dd_mom = f"{dscr_last - dscr_prev:+.2f}x" if (dscr_last and dscr_prev) else "\u2014"
    dd_yoy = f"{dscr_last - dscr_y:+.2f}x" if (dscr_last and dscr_y) else "\u2014"
    bc = budget_cells("DSCR", is_budget_row=False)
    row = f"| DSCR | {dl} |"
    row += f" {bc[0]} | {bc[1]} |"
    row += f" {dp} | {dd_mom} |"
    if has_yoy:
        row += f" {dy} | {dd_yoy} |"
    row += f" {bc[2]} | {bc[3]} | {bc[4]} |"
    lines.append(row)

    # NOI/Unit/Mo
    c_nu = summaries[last]["NOI/Unit/Mo"]
    p_nu = summaries[prev]["NOI/Unit/Mo"]
    y_nu = prior_summaries[yoy_month]["NOI/Unit/Mo"] if has_yoy else None
    add_comp("NOI/Unit/Mo", c_nu, p_nu, y_nu, is_budget_row=False)

    lines.append("")
    if has_budget:
        lines.append("\u0394 Bud / YTD Var are favorable variances (positive = better "
                     "than budget: more income or less expense), per AppFolio. "
                     f"YTD is calendar year {ly} through {month_names[lm - 1]}.")
    else:
        lines.append("_Budget columns are empty: no budget on file for this property "
                     "(or the budget fetch was unavailable)._")
    lines.append("")
    return "\n".join(lines) + "\n"


def _budget_lookup(struct, label):
    """Return (budget, actual, favorable_variance) for a label, or None.

    Total/derived labels (Total Income, NOI, etc.) are stored as
    (actual, budget, var) tuples directly on the struct; GL line labels live
    in the lb/la/lv dicts.
    """
    if struct is None:
        return None
    if label in ("Total Income", "Total OpEx", "NOI", "Total Deductions", "Cashflow"):
        a, b, v = struct[label]
        return (b, a, v)
    if label in struct["lb"] or label in struct["la"]:
        return (struct["lb"].get(label, 0.0), struct["la"].get(label, 0.0),
                struct["lv"].get(label, 0.0))
    # Known P&L line with no budget rows posted -> treat as zero budget
    if label in INCOME_ROWS or label in OPEX_ROWS or label in BELOW_NOI_ROWS:
        return (0.0, 0.0, 0.0)
    return None


def build_commentary(monthly, summaries, months, anchor_idx=None):
    """Auto-generate commentary from top 3 movers (anchored on latest complete month)."""
    if anchor_idx is None:
        anchor_idx = len(months) - 1
    last = months[anchor_idx]
    prev = months[anchor_idx - 1]
    last_m = monthly.get(last, {})
    prev_m = monthly.get(prev, {})

    all_labels = INCOME_ROWS + OPEX_ROWS + BELOW_NOI_ROWS
    movers = []
    for label in all_labels:
        cur = last_m.get(label, 0)
        prv = prev_m.get(label, 0)
        diff = cur - prv
        if abs(diff) > 0:
            movers.append((label, diff, cur, prv))

    movers.sort(key=lambda x: abs(x[1]), reverse=True)

    parts = []
    for label, diff, cur, prv in movers[:3]:
        direction = "increased" if diff > 0 else "decreased"
        if prv != 0:
            pct = abs(diff / prv) * 100
            parts.append(f"{label} {direction} by {abs(round(diff)):,} ({pct:.0f}%)")
        else:
            parts.append(f"{label} {direction} by {abs(round(diff)):,} from zero")

    noi_diff = summaries[last]["NOI"] - summaries[prev]["NOI"]
    noi_dir = "up" if noi_diff > 0 else "down"

    if parts:
        c = f"Month-over-month, the biggest movers were: {'; '.join(parts)}. "
        c += f"NOI was {noi_dir} {abs(round(noi_diff)):,} overall."
    else:
        c = "No significant month-over-month changes."
    return c


def build_validation(monthly, summaries, months, af_totals, units):
    """Build validation checks section."""
    lines = ["## Validation", ""]
    lines.append("| Check | Result |")
    lines.append("| ------ | ------ |")

    # Income cross-check
    income_mismatches = 0
    for m in months:
        af = af_totals.get(m, {})
        if "Total Income" in af:
            computed = summaries[m]["Total Income"]
            expected = af["Total Income"]
            if abs(computed - expected) > 1:
                income_mismatches += 1
    rounding_months = sum(1 for m in months
                          if m in af_totals and "Total Income" in af_totals[m]
                          and 0 < abs(summaries[m]["Total Income"] - af_totals[m]["Total Income"]) <= 1)
    if income_mismatches == 0:
        if rounding_months > 0:
            lines.append(f"| Income line items = AF Total | Pass (within $1 rounding on {rounding_months} of 12 months) |")
        else:
            lines.append("| Income line items = AF Total | Pass |")
    else:
        lines.append(f"| Income line items = AF Total | FAIL ({income_mismatches} months diverge) |")

    # Expense cross-check
    exp_mismatches = 0
    exp_rounding = 0
    for m in months:
        af = af_totals.get(m, {})
        if "Total Expense" in af:
            computed = summaries[m]["Total OpEx"] + summaries[m]["Total Deductions"]
            expected = af["Total Expense"]
            diff = abs(computed - expected)
            if diff > 2:
                exp_mismatches += 1
            elif diff > 0:
                exp_rounding += 1
    if exp_mismatches == 0:
        if exp_rounding > 0:
            lines.append(f"| OpEx + Below-NOI = AF Expense | Pass (within $2 rounding on {exp_rounding} of 12 months) |")
        else:
            lines.append("| OpEx + Below-NOI = AF Expense | Pass |")
    else:
        lines.append(f"| OpEx + Below-NOI = AF Expense | FAIL ({exp_mismatches} months diverge) |")

    # T-12 NOI positive
    t12_noi = sum(summaries[m]["NOI"] for m in months)
    lines.append(f"| T-12 NOI positive | {'Pass' if t12_noi > 0 else 'FAIL'} (${t12_noi:,.0f}) |")

    # R&M concentration
    max_rm_pct = 0
    max_rm_month = ""
    for m in months:
        rm = sum(monthly.get(m, {}).get(r, 0) for r in ["R&M Repairs", "R&M Turns", "R&M Grounds"])
        inc = summaries[m]["Total Income"]
        if inc > 0:
            pct = rm / inc * 100
            if pct > max_rm_pct:
                max_rm_pct = pct
                max_rm_month = m
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if max_rm_pct > 30:
        mm_idx = int(max_rm_month[5:7]) - 1
        lines.append(f"| R&M > 30% of income any month | FLAG ({max_rm_pct:.1f}% in {month_names[mm_idx]}) |")
    else:
        if max_rm_month:
            mm_idx = int(max_rm_month[5:7]) - 1
            lines.append(f"| R&M > 30% of income any month | No flags (max {max_rm_pct:.1f}% in {month_names[mm_idx]}) |")

    # Vacancy rate
    t12_vacancy = sum(monthly.get(m, {}).get("Rent Income", 0) for m in months)
    # Vacancy is embedded in Rent Income via GL 40120; to extract it we'd need raw GL
    # For now, skip or approximate

    lines.append("")
    return "\n".join(lines) + "\n"


def build_summary(summaries, months, cap_rate, units, t12_vals,
                  prior_summaries, occupancy_data, anchor_idx=None,
                  budget_month=None):
    """Build a compact Summary section.

    Layout per row: current month value, budget, T-1 (prior month), T-12
    (same month a year ago), then the three deltas (Δ Bud, Δ MoM, Δ YoY).
    Deltas are whole-percent (or whole-pp for occupancy). Budget applies only
    to monthly NOI / Cashflow; trailing and occupancy rows show em-dashes.

    Anchors on the latest complete month (anchor_idx) so the headline never
    reflects a partial month with unposted expenses.
    """
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    if anchor_idx is None:
        anchor_idx = len(months) - 1
    latest = months[anchor_idx]
    prior = months[anchor_idx - 1]
    ly, lm = int(latest[:4]), int(latest[5:7])
    yoy_key = f"{ly - 1:04d}-{lm:02d}"

    latest_label = f"{month_names[lm - 1]} {str(ly)[2:]}"

    s_latest = summaries[latest]
    s_prior = summaries[prior]
    s_yoy = prior_summaries.get(yoy_key) if prior_summaries else None

    def fmt_dollar(v):
        if v < 0:
            return f"({abs(v):,.0f})"
        return f"{v:,.0f}"

    def pct_delta(curr, prev):
        if curr is None or prev is None:
            return "—"
        if prev == 0:
            return "n/m"
        pct = (curr - prev) / abs(prev) * 100
        return f"{pct:+.0f}%"

    def occ_pct(month_key):
        if not occupancy_data or month_key not in occupancy_data:
            return None
        occ, total = occupancy_data[month_key]
        return (occ / total * 100) if total else None

    def occ_delta(curr_pct, prev_pct):
        if curr_pct is None or prev_pct is None:
            return "—"
        return f"{curr_pct - prev_pct:+.0f}pp"

    def dollar_or_dash(v):
        return fmt_dollar(v) if v is not None else "—"

    def budget_val(key):
        """Monthly budget for a total label (NOI / Cashflow), or None."""
        if budget_month is None or key not in budget_month:
            return None
        return budget_month[key][1]  # (actual, budget, variance) -> budget

    prev_y, prev_m = int(prior[:4]), int(prior[5:7])
    prior_label = f"{month_names[prev_m - 1]} {str(prev_y)[2:]}"
    yoy_label = f"{month_names[lm - 1]} {str(ly - 1)[2:]}"

    noi = s_latest["NOI"]
    cf = s_latest["Cashflow"]
    occ = occ_pct(latest)
    t12_noi = t12_vals.get(latest) if t12_vals else sum(summaries[m]["NOI"] for m in months)
    implied = (t12_noi / cap_rate) if (t12_noi and cap_rate) else None

    lines = ["## Summary", ""]
    lines.append(f"| Metric | {latest_label} | Budget | {prior_label} | {yoy_label} | Δ Bud | Δ MoM | Δ YoY |")
    lines.append("|:-------|-------:|-------:|-------:|-------:|------:|------:|------:|")

    # NOI
    noi_bud = budget_val("NOI")
    noi_yoy = s_yoy["NOI"] if s_yoy else None
    lines.append(f"| NOI | {fmt_dollar(noi)} | {dollar_or_dash(noi_bud)} | {fmt_dollar(s_prior['NOI'])} | "
                 f"{dollar_or_dash(noi_yoy)} | {pct_delta(noi, noi_bud)} | "
                 f"{pct_delta(noi, s_prior['NOI'])} | {pct_delta(noi, noi_yoy)} |")

    # Cashflow
    cf_bud = budget_val("Cashflow")
    cf_yoy = s_yoy["Cashflow"] if s_yoy else None
    lines.append(f"| Cashflow | {fmt_dollar(cf)} | {dollar_or_dash(cf_bud)} | {fmt_dollar(s_prior['Cashflow'])} | "
                 f"{dollar_or_dash(cf_yoy)} | {pct_delta(cf, cf_bud)} | "
                 f"{pct_delta(cf, s_prior['Cashflow'])} | {pct_delta(cf, cf_yoy)} |")

    # Occupancy (no budget; deltas in percentage points)
    occ_prior = occ_pct(prior)
    occ_yoy = occ_pct(yoy_key)
    occ_str = f"{occ:.1f}%" if occ is not None else "—"
    occ_prior_str = f"{occ_prior:.1f}%" if occ_prior is not None else "—"
    occ_yoy_str = f"{occ_yoy:.1f}%" if occ_yoy is not None else "—"
    lines.append(f"| Occupancy | {occ_str} | — | {occ_prior_str} | {occ_yoy_str} | — | "
                 f"{occ_delta(occ, occ_prior)} | {occ_delta(occ, occ_yoy)} |")

    # T-12 NOI (trailing; no budget)
    if t12_noi is not None:
        t12_prior = t12_vals.get(prior) if t12_vals else None
        t12_yoy = t12_vals.get(yoy_key) if t12_vals else None
        lines.append(f"| T-12 NOI | {t12_noi:,.0f} | — | {dollar_or_dash(t12_prior)} | "
                     f"{dollar_or_dash(t12_yoy)} | — | {pct_delta(t12_noi, t12_prior)} | "
                     f"{pct_delta(t12_noi, t12_yoy)} |")

    # Implied Value (trailing; no budget)
    if implied is not None:
        iv_prior = (t12_vals.get(prior) / cap_rate) if t12_vals and t12_vals.get(prior) else None
        iv_yoy_val = t12_vals.get(yoy_key) if t12_vals else None
        iv_yoy = (iv_yoy_val / cap_rate) if iv_yoy_val else None
        iv_prior_str = f"{iv_prior / 1000:,.0f}K" if iv_prior is not None else "—"
        iv_yoy_str = f"{iv_yoy / 1000:,.0f}K" if iv_yoy is not None else "—"
        lines.append(f"| Implied Value | {implied / 1000:,.0f}K | — | {iv_prior_str} | {iv_yoy_str} | — | "
                     f"{pct_delta(implied, iv_prior)} | {pct_delta(implied, iv_yoy)} |")

    lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def period_folder(end_month):
    """Reporting-period folder, e.g. '2026/04-April'. end_month is 'YYYY-MM'
    and already represents the month the report covers, so reports file under
    <year>/<NN-Month>/ and sort chronologically by the period, not the run."""
    names = ["January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]
    y, m = int(end_month[:4]), int(end_month[5:7])
    return f"{y}/{m:02d}-{names[m - 1]}"


def generate_full_report(code, prop, monthly, summaries, af_totals, months, labels,
                         cap_rate, units, report_date, report_date_iso,
                         prior_monthly=None, prior_summaries=None, prior_t12_noi=None,
                         gpr_monthly=None, lease_history=None, lease_snapshot=None,
                         lease_today=None,
                         occupancy_data=None, anchor_idx=None,
                         budget_month=None, budget_ytd=None):
    """Generate the complete markdown report."""
    addr = prop["addr"]
    fund = prop["fund"]

    first_m = months[0]
    last_m = months[-1]
    fy1, fm1 = int(first_m[:4]), int(first_m[5:7])
    fy2, fm2 = int(last_m[:4]), int(last_m[5:7])
    month_names_full = ["January", "February", "March", "April", "May", "June",
                        "July", "August", "September", "October", "November", "December"]
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    period = f"{month_names_full[fm1 - 1]} {fy1} \u2013 {month_names_full[fm2 - 1]} {fy2}"
    period_short = f"({month_names[fm1 - 1]} {fy1} \u2013 {month_names[fm2 - 1]} {fy2})"

    r = []
    r.append("---")
    r.append(f'title: "{code.upper()} Trailing 12-Month P&L"')
    r.append(f"date: {report_date_iso}")
    r.append("type: report")
    r.append(f"tags: [m5x2, {code}, pnl]")
    r.append("source: appfolio")
    r.append("---")
    r.append(f"# {code.upper()} \u2014 {addr}")
    r.append(f"## Trailing 12-Month P&L {period_short}")
    r.append("")
    r.append("Source: AppFolio income statement (12-month detail view, accrual basis).")
    r.append("")

    # Pre-compute derived metrics and T-12 vals (needed by Summary)
    prior_monthly_noi = {}
    if prior_summaries:
        for pm_key, ps in prior_summaries.items():
            prior_monthly_noi[pm_key] = ps["NOI"]

    sreo_val = SREO_VALUES.get(code)
    dm_text, t12_vals = build_derived_metrics(summaries, months, labels, cap_rate, units, prior_monthly_noi, sreo_val)

    # Inject prior-year T-12 NOI into t12_vals for YoY
    if prior_t12_noi is not None:
        ly, lm = int(months[-1][:4]), int(months[-1][5:7])
        yoy_key = f"{ly - 1:04d}-{lm:02d}"
        if yoy_key not in t12_vals:
            t12_vals[yoy_key] = prior_t12_noi

    # Summary (top section)
    r.append(build_summary(summaries, months, cap_rate, units, t12_vals,
                           prior_summaries, occupancy_data, anchor_idx, budget_month))

    # Main P&L table
    r.append(build_pnl_table(monthly, summaries, months, labels))

    # Cross-check warnings
    mismatches = []
    for m in months:
        af = af_totals.get(m, {})
        if "Total Income" in af:
            computed = summaries[m]["Total Income"]
            expected = af["Total Income"]
            if abs(computed - expected) > 1:
                mismatches.append(f"{m}: Income computed={computed:.0f} vs AppFolio={expected:.0f}")
        if "Total Expense" in af:
            computed = summaries[m]["Total OpEx"] + summaries[m]["Total Deductions"]
            expected = af["Total Expense"]
            if abs(computed - expected) > 1:
                mismatches.append(f"{m}: Expense computed={computed:.0f} vs AppFolio={expected:.0f}")
    if mismatches:
        r.append("> **Cross-check warnings** (computed vs AppFolio totals):")
        for mm in mismatches:
            r.append(f"> - {mm}")
        r.append("")

    # Derived metrics
    r.append(dm_text)

    # Equity (SREO Value - outstanding debt) + ROE / RORE.
    # T-12 cashflow and debt paydown use the clean anchor-based window (matching
    # the Summary/JSON), so a partial latest month doesn't understate the flows.
    _aidx = anchor_idx if anchor_idx is not None else len(months) - 1
    _eq_window = range(_aidx - 11, _aidx + 1)

    def _cf_at(j):
        if 0 <= j < len(months):
            return summaries[months[j]]["Cashflow"]
        pk = _prior_month_key(months[0], j)
        if prior_summaries and pk in prior_summaries:
            return prior_summaries[pk]["Cashflow"]
        return 0.0

    t12_cashflow = sum(_cf_at(j) for j in _eq_window)
    t12_principal = sum(monthly[months[j]].get("Mortgage Principal", 0.0)
                        for j in _eq_window if 0 <= j < len(months))

    equity_text, _ = build_equity(code, sreo_val, SREO_VALUES_1YR_AGO.get(code),
                                  DEBT_BALANCES.get(code), t12_cashflow, t12_principal)
    r.append(equity_text)

    # Lease Activity & Exposure (snapshot + CY-1/CY activity matrix)
    r.append(build_lease_activity(lease_history, lease_snapshot, units,
                                  lease_today or date.today()))

    # Ancillary Income
    if gpr_monthly is None:
        gpr_monthly = {}
    anc_text, _, _, _ = build_ancillary_income(monthly, months, gpr_monthly)
    r.append(anc_text)

    # Comparisons
    r.append(build_comparisons(monthly, summaries, months, labels, cap_rate, units,
                               t12_vals, prior_monthly, prior_summaries,
                               anchor_idx, budget_month, budget_ytd))

    # Commentary
    r.append(build_commentary(monthly, summaries, months, anchor_idx))
    r.append("")

    # Validation
    r.append(build_validation(monthly, summaries, months, af_totals, units))

    r.append(f"Generated from AppFolio on {report_date}.")

    return "\n".join(r) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate trailing 12-month P&L from AppFolio")
    parser.add_argument("property", help="Property code (e.g. s300, b101)")
    parser.add_argument("--date", help="End month YYYY-MM (default: previous full month)", default=None)
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout, don't write file")
    args = parser.parse_args()

    code = args.property.lower()
    if code not in PROPERTIES:
        print(json.dumps({"error": f"Unknown property: {code}", "valid": list(PROPERTIES.keys())}))
        sys.exit(1)

    prop = PROPERTIES[code]

    # Determine end month
    if args.date:
        end_month = args.date
    else:
        today = date.today()
        # Use previous full month
        if today.month == 1:
            end_month = f"{today.year - 1}-12"
        else:
            end_month = f"{today.year}-{today.month - 1:02d}"

    # Compute month range
    ey, em = int(end_month[:4]), int(end_month[5:7])
    sy = ey - 1 if em < 12 else ey
    sm = em + 1 if em < 12 else 1
    if em == 12:
        sy = ey
        sm = 1
    else:
        sm = em + 1
        sy = ey - 1
    start_month = f"{sy}-{sm:02d}"

    months, labels = compute_months(end_month)

    # Compute prior-year range (for historical T-12 and YoY)
    prior_end_y, prior_end_m = ey - 1, em
    prior_start_y = prior_end_y - 1 if prior_end_m < 12 else prior_end_y
    prior_start_m = prior_end_m + 1 if prior_end_m < 12 else 1
    if prior_end_m == 12:
        prior_start_y = prior_end_y
        prior_start_m = 1
    else:
        prior_start_m = prior_end_m + 1
        prior_start_y = prior_end_y - 1
    prior_start = f"{prior_start_y}-{prior_start_m:02d}"
    prior_end = f"{prior_end_y}-{prior_end_m:02d}"
    prior_months, _ = compute_months(prior_end)

    print(f"Fetching {code} data from AppFolio...", file=sys.stderr)

    # Fetch current year 12-month statement
    try:
        raw_current = fetch_12m_income_statement(prop["ids"], start_month, end_month)
    except urllib.error.HTTPError as e:
        print(json.dumps({"error": f"AppFolio API error: {e.code} {e.reason}"}))
        sys.exit(1)

    # Fetch prior year for YoY
    try:
        raw_prior = fetch_prior_year_12m(prop["ids"], prior_start, prior_end)
    except urllib.error.HTTPError as e:
        print(f"Warning: Could not fetch prior year data: {e.code}", file=sys.stderr)
        raw_prior = None

    # Fetch lease data: full occupancy history (activity flows) + per-unit snapshot.
    lease_today = date.today()
    lease_history = None
    lease_snapshot = None
    try:
        lease_history = fetch_lease_history(prop["ids"], lease_today.year - 1, lease_today.year)
    except Exception as e:
        print(f"Warning: Could not fetch lease history: {e}", file=sys.stderr)
    try:
        lease_snapshot = fetch_unit_snapshot(prop["ids"], lease_today.isoformat())
    except Exception as e:
        print(f"Warning: Could not fetch unit snapshot: {e}", file=sys.stderr)

    # Parse
    monthly, af_totals, _, gpr_monthly = parse_api_rows(raw_current, months)
    prior_monthly = None
    prior_summaries = None
    prior_t12_noi = None
    if raw_prior:
        prior_monthly, _, _, _ = parse_api_rows(raw_prior, prior_months)
        prior_summaries = compute_summaries(prior_monthly, prior_months, prop["units"])
        prior_t12_noi = sum(prior_summaries[m]["NOI"] for m in prior_months)

    # Compute
    summaries = compute_summaries(monthly, months, prop["units"])
    cap_rate = prop["cap"]
    units = prop["units"]

    # Anchor on the latest complete month (income + expense both posted).
    # Booking lag often leaves the most recent month with income but no expense.
    anchor_idx = find_anchor_idx(months, af_totals)
    anchor_month = months[anchor_idx]
    if anchor_idx != len(months) - 1:
        print(f"Note: anchoring comparisons on {anchor_month} "
              f"(latest month with both income and expense posted).", file=sys.stderr)

    # Fetch budget vs actual for the anchor month and calendar YTD (Jan..anchor)
    ay = int(anchor_month[:4])
    ytd_start = f"{ay}-01"
    budget_month = None
    budget_ytd = None
    try:
        budget_month = make_budget_struct(
            fetch_budget_comparison(prop["ids"], anchor_month, anchor_month))
        budget_ytd = make_budget_struct(
            fetch_budget_comparison(prop["ids"], ytd_start, anchor_month))
    except Exception as e:
        print(f"Warning: Could not fetch budget comparison: {e}", file=sys.stderr)

    # Report date
    today = date.today()
    report_date = today.strftime("%Y.%m.%d")
    report_date_iso = today.strftime("%Y-%m-%d")

    # Fetch occupancy for summary (anchor month, its prior month, YoY)
    latest_m = anchor_month
    prior_m = months[anchor_idx - 1]
    lmy, lmm = int(latest_m[:4]), int(latest_m[5:7])
    yoy_m = f"{lmy - 1:04d}-{lmm:02d}"
    occupancy_data = {}
    try:
        occupancy_data = fetch_occupancy_trio(prop["ids"], latest_m, prior_m, yoy_m)
    except Exception as e:
        print(f"Warning: Could not fetch occupancy data: {e}", file=sys.stderr)

    # Generate
    report = generate_full_report(
        code, prop, monthly, summaries, af_totals, months, labels,
        cap_rate, units, report_date, report_date_iso,
        prior_monthly, prior_summaries, prior_t12_noi,
        gpr_monthly, lease_history, lease_snapshot, lease_today, occupancy_data,
        anchor_idx, budget_month, budget_ytd,
    )

    if args.dry_run:
        print(report)
    else:
        # Determine output path
        period = period_folder(end_month)
        out_dir = REPORTS_BASE / period / prop["fund"]
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{report_date}-{code}-trailing-12m-pnl.md"
        out_path = out_dir / filename
        with open(out_path, "w") as f:
            f.write(report)
        print(f"Wrote: {out_path}", file=sys.stderr)

    # Summary JSON to stdout — use the clean T-12 ending at the anchor month
    # (the full window may include a partial latest month with unposted expenses).
    def _metric_at(idx, key):
        if 0 <= idx < len(months):
            return summaries[months[idx]][key]
        pk = _prior_month_key(months[0], idx)
        if prior_summaries and pk in prior_summaries:
            return prior_summaries[pk][key]
        return 0

    window = range(anchor_idx - 11, anchor_idx + 1)
    t12_noi = sum(_metric_at(j, "NOI") for j in window)
    t12_income = sum(_metric_at(j, "Total Income") for j in window)
    t12_opex = sum(_metric_at(j, "Total OpEx") for j in window)
    t12_cf = sum(_metric_at(j, "Cashflow") for j in window)
    t12_principal = sum(monthly[months[j]].get("Mortgage Principal", 0.0)
                        for j in window if 0 <= j < len(months))
    eq_ret = compute_equity_returns(SREO_VALUES.get(code), SREO_VALUES_1YR_AGO.get(code),
                                    DEBT_BALANCES.get(code), t12_cf, t12_principal)
    last_dscr = summaries[anchor_month]["DSCR"]

    # Compute ancillary metrics for JSON
    _, _, ancillary_pct, arrears_pct = build_ancillary_income(monthly, months, gpr_monthly)

    summary = {
        "property": code,
        "period": f"{months[0]} to {months[-1]}",
        "T12_income": round(t12_income),
        "T12_opex": round(t12_opex),
        "T12_NOI": round(t12_noi),
        "T12_cashflow": round(t12_cf),
        "implied_value": round(t12_noi / cap_rate) if cap_rate else None,
        "sreo_value": SREO_VALUES.get(code) * 1000 if SREO_VALUES.get(code) else None,
        "debt": DEBT_BALANCES.get(code),
        "equity": (SREO_VALUES.get(code) * 1000 - DEBT_BALANCES.get(code))
                  if (SREO_VALUES.get(code) and DEBT_BALANCES.get(code) is not None) else None,
        "realizable_equity": (SREO_VALUES.get(code) * 1000 * 0.91 - DEBT_BALANCES.get(code))
                  if (SREO_VALUES.get(code) and DEBT_BALANCES.get(code) is not None) else None,
        "value_1yr_ago": SREO_VALUES_1YR_AGO.get(code) * 1000 if SREO_VALUES_1YR_AGO.get(code) else None,
        "debt_paydown_t12": round(t12_principal),
        "change_in_value": round(eq_ret["change_in_value"]) if eq_ret["change_in_value"] is not None else None,
        "total_return": round(eq_ret["numerator"]) if eq_ret["numerator"] is not None else None,
        "equity_1yr_ago": round(eq_ret["equity_1yr"]) if eq_ret["equity_1yr"] is not None else None,
        "realizable_equity_1yr_ago": round(eq_ret["realizable_1yr"]) if eq_ret["realizable_1yr"] is not None else None,
        "ROE": round(eq_ret["roe"], 4) if eq_ret["roe"] is not None else None,
        "RORE": round(eq_ret["rore"], 4) if eq_ret["rore"] is not None else None,
        "DSCR_last_month": round(last_dscr, 2) if last_dscr else None,
        "NOI_per_unit_mo": round(t12_noi / 12 / units),
        "ancillary_pct_gpr": round(ancillary_pct, 1),
        "arrears_billing_pct": round(arrears_pct, 1),
    }
    if not args.dry_run:
        summary["file"] = str(out_path)

    print(json.dumps(summary))


if __name__ == "__main__":
    main()
