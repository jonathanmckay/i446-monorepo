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
    "p705": {"fund": "fund-0", "units": 4, "ids": ["40"], "cap": 0.075, "addr": "705 S Perry St, Spokane WA 99202"},
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
OPEX_OTHER_CODES = {"53001", "50800"}

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


def appfolio_post(endpoint, payload):
    """POST to AppFolio API, return parsed JSON."""
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
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_12m_income_statement(prop_ids, from_month, to_month):
    """Fetch the 12-month income statement. Returns raw API rows."""
    payload = {
        "posted_on_from": from_month,
        "posted_on_to": to_month,
        "property_visibility": "active",
        "fund_type": "all",
        "level_of_detail": "detail_view",
        "include_zero_balance_gl_accounts": "0",
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
        "properties": {"properties_ids": prop_ids},
    }
    return appfolio_post("twelve_month_income_statement.json", payload)


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

            if code_str in ACCOUNT_LOOKUP:
                _, label = ACCOUNT_LOOKUP[code_str]
                monthly[mk][label] += val

    return dict(monthly), dict(af_totals), dict(gl_lines)


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


def fmt_delta(cur, prev):
    if cur is None or prev is None:
        return "\u2014"
    diff = cur - prev
    if prev == 0:
        return "0 (0.0%)" if diff == 0 else "n/m"
    if (prev > 0 and cur < 0) or (prev < 0 and cur > 0):
        pct = (diff / abs(prev)) * 100
        sign = "+" if diff > 0 else ""
        return f"{sign}{round(diff):,} (sign flip)"
    pct = (diff / abs(prev)) * 100
    sign = "+" if diff > 0 else ""
    return f"{sign}{round(diff):,} ({sign}{pct:.1f}%)"


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


def build_derived_metrics(summaries, months, labels, cap_rate, units, prior_monthly_noi=None):
    """Build derived metrics table. prior_monthly_noi is dict[month] -> NOI for prior year months."""
    lines = ["### Derived Metrics", ""]
    hdr = "| Metric |" + "".join(f" {l} |" for l in labels)
    sep = "|:-------|" + " -------:|" * len(labels)
    lines.extend([hdr, sep])
    cap_pct = f"{cap_rate*100:.1f}%"

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
            if i < 11:
                t12_vals[m] = None
                row += " \u2014 |"
            else:
                t12 = sum(summaries[months[j]]["NOI"] for j in range(0, 12))
                t12_vals[m] = t12
                row += f" {fmt(t12)} |"
    lines.append(row)

    # Implied Value
    row = f"| Implied Value ({cap_pct}) |"
    for m in months:
        if t12_vals.get(m) is not None:
            row += f" {fmt_k(t12_vals[m] / cap_rate)} |"
        else:
            row += " \u2014 |"
    lines.append(row)

    # T-3 Ann Value
    row = f"| T-3 Ann Value ({cap_pct}) |"
    for i, m in enumerate(months):
        if i < 2:
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

    lines.append(f"T-3 Ann NOI = average of trailing 3 months NOI x 12.")
    lines.append(f"T-12 NOI = rolling 12-month sum ending at that month.")
    lines.append(f"Implied Value = T-12 NOI / {cap_pct}.")
    lines.append("")
    return "\n".join(lines) + "\n", t12_vals


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


def build_historical_table(months, t12_vals, cap_rate, prior_t12_noi=None):
    """Build the historical T-12 NOI table."""
    cap_pct = f"{cap_rate*100:.1f}%"
    lines = ["## Historical T-12 NOI & Implied Valuation", ""]
    lines.append(f"| Month | T-12 NOI | Implied Value ({cap_pct}) |")
    lines.append("| -------- | -------: | -------------------: |")

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    # Include prior-year T-12 NOI as the first row if available
    if prior_t12_noi is not None:
        first_m = months[0]
        y, m = int(first_m[:4]), int(first_m[5:7])
        # Prior month is one month before our window
        pm = m - 1
        py = y
        if pm <= 0:
            pm += 12
            py -= 1
        label = f"{month_names[pm - 1]} {py}"
        lines.append(f"| {label} | {fmt(prior_t12_noi)} | {fmt_k(prior_t12_noi / cap_rate)} |")

    for mk in months:
        if t12_vals.get(mk) is not None:
            y, m = int(mk[:4]), int(mk[5:7])
            label = f"{month_names[m - 1]} {y}"
            lines.append(f"| {label} | {fmt(t12_vals[mk])} | {fmt_k(t12_vals[mk] / cap_rate)} |")

    lines.append("")
    return "\n".join(lines) + "\n"


def build_comparisons(monthly, summaries, months, labels, cap_rate, units, t12_vals,
                      prior_monthly=None, prior_summaries=None):
    """Build the comparisons section with full GL detail."""
    lines = ["## Comparisons", ""]
    last = months[-1]
    prev = months[-2]

    ly, lm = int(last[:4]), int(last[5:7])
    py, pm_num = ly - 1, lm
    yoy_month = f"{py:04d}-{pm_num:02d}"

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    last_label = f"{month_names[lm - 1]} {str(ly)[2:]}"
    prev_y, prev_m_num = int(prev[:4]), int(prev[5:7])
    prev_label = f"{month_names[prev_m_num - 1]} {str(prev_y)[2:]}"
    yoy_label = f"{month_names[pm_num - 1]} {str(py)[2:]}"

    has_yoy = prior_summaries is not None and yoy_month in prior_summaries

    hdr = f"| Account | {last_label} | {prev_label} | \u0394 MoM |"
    sep_line = "|:--------|-------:|-------:|:------|"
    if has_yoy:
        hdr += f" {yoy_label} | \u0394 YoY |"
        sep_line += "-------:|:------|"
    lines.extend([hdr, sep_line])

    def add_comp(label, cur, prv, yoy=None, bold=False):
        lbl = f"**{label}**" if bold else label
        row = f"| {lbl} | {fmt(cur, bold)} | {fmt(prv, bold)} | {fmt_delta(cur, prv)} |"
        if has_yoy:
            yoy_fmt = fmt(yoy, bold) if yoy is not None else "\u2014"
            yoy_delta = fmt_delta(cur, yoy) if yoy is not None else "\u2014"
            row += f" {yoy_fmt} | {yoy_delta} |"
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

    # Derived metrics
    t12_last = t12_vals.get(last)
    t12_prev = t12_vals.get(prev)
    add_comp("T-12 NOI", t12_last, t12_prev,
             t12_vals.get(yoy_month) if has_yoy else None)
    if t12_last is not None:
        iv_last = t12_last / cap_rate
        iv_prev = t12_prev / cap_rate if t12_prev else None
        yoy_iv = None
        # Custom row for Implied Value (use K format)
        iv_last_s = fmt_k(iv_last)
        iv_prev_s = fmt_k(iv_prev) if iv_prev else "\u2014"
        if iv_last is not None and iv_prev is not None:
            iv_diff = round(iv_last / 1000) - round(iv_prev / 1000)
            iv_pct = (iv_last - iv_prev) / abs(iv_prev) * 100 if iv_prev else 0
            sign = "+" if iv_diff > 0 else ""
            iv_delta = f"{sign}{iv_diff:,}K ({sign}{iv_pct:.1f}%)"
        else:
            iv_delta = "\u2014"
        row = f"| Implied Value | {iv_last_s} | {iv_prev_s} | {iv_delta} |"
        if has_yoy:
            row += " \u2014 | \u2014 |"
        lines.append(row)

    # DSCR
    dscr_last = summaries[last]["DSCR"]
    dscr_prev = summaries[prev]["DSCR"]
    dscr_y = prior_summaries[yoy_month]["DSCR"] if has_yoy else None
    dl = f"{dscr_last:.2f}x" if dscr_last else "\u2014"
    dp = f"{dscr_prev:.2f}x" if dscr_prev else "\u2014"
    dy = f"{dscr_y:.2f}x" if dscr_y else "\u2014"
    if dscr_last and dscr_prev and dscr_prev != 0:
        diff = dscr_last - dscr_prev
        pct = (diff / abs(dscr_prev)) * 100
        sign = "+" if diff > 0 else ""
        dd_mom = f"{sign}{diff:.2f}x ({sign}{pct:.1f}%)"
    else:
        dd_mom = "\u2014"
    if dscr_last and dscr_y and dscr_y != 0:
        diff = dscr_last - dscr_y
        pct = (diff / abs(dscr_y)) * 100
        sign = "+" if diff > 0 else ""
        dd_yoy = f"{sign}{diff:.2f}x ({sign}{pct:.1f}%)"
    else:
        dd_yoy = "\u2014"
    row = f"| DSCR | {dl} | {dp} | {dd_mom} |"
    if has_yoy:
        row += f" {dy} | {dd_yoy} |"
    lines.append(row)

    # NOI/Unit/Mo
    c_nu = summaries[last]["NOI/Unit/Mo"]
    p_nu = summaries[prev]["NOI/Unit/Mo"]
    y_nu = prior_summaries[yoy_month]["NOI/Unit/Mo"] if has_yoy else None
    add_comp("NOI/Unit/Mo", c_nu, p_nu, y_nu)

    lines.append("")
    return "\n".join(lines) + "\n"


def build_commentary(monthly, summaries, months):
    """Auto-generate commentary from top 3 movers."""
    last = months[-1]
    prev = months[-2]
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


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def determine_quarter(end_month):
    """Determine fiscal quarter folder from end month."""
    y, m = int(end_month[:4]), int(end_month[5:7])
    if m <= 3:
        return f"{y}.q1"
    elif m <= 6:
        return f"{y}.q1"  # Q1 reports cover through April
    # Actually, let's use the calendar: Jan-Mar=q1, Apr-Jun=q2, Jul-Sep=q3, Oct-Dec=q4
    # But the existing reports are in 2026.q1/ even though they cover through April.
    # Follow convention: just use year.q{quarter}
    q = (m - 1) // 3 + 1
    return f"{y}.q{q}"


def generate_full_report(code, prop, monthly, summaries, af_totals, months, labels,
                         cap_rate, units, report_date, report_date_iso,
                         prior_monthly=None, prior_summaries=None, prior_t12_noi=None):
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
    r.append("Source: AppFolio income statement (12-month detail view).")
    r.append("")

    # Main table
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

    # Build prior monthly NOI map for T-12 rolling
    prior_monthly_noi = {}
    if prior_summaries:
        for pm_key, ps in prior_summaries.items():
            prior_monthly_noi[pm_key] = ps["NOI"]

    # Derived metrics
    dm_text, t12_vals = build_derived_metrics(summaries, months, labels, cap_rate, units, prior_monthly_noi)
    r.append(dm_text)

    # Historical table
    r.append(build_historical_table(months, t12_vals, cap_rate, prior_t12_noi))

    # Comparisons
    r.append(build_comparisons(monthly, summaries, months, labels, cap_rate, units,
                               t12_vals, prior_monthly, prior_summaries))

    # Commentary
    r.append(build_commentary(monthly, summaries, months))
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

    # Fetch prior year for historical + YoY
    try:
        raw_prior = fetch_prior_year_12m(prop["ids"], prior_start, prior_end)
    except urllib.error.HTTPError as e:
        print(f"Warning: Could not fetch prior year data: {e.code}", file=sys.stderr)
        raw_prior = None

    # Parse
    monthly, af_totals, _ = parse_api_rows(raw_current, months)
    prior_monthly = None
    prior_summaries = None
    prior_t12_noi = None
    if raw_prior:
        prior_monthly, _, _ = parse_api_rows(raw_prior, prior_months)
        prior_summaries = compute_summaries(prior_monthly, prior_months, prop["units"])
        prior_t12_noi = sum(prior_summaries[m]["NOI"] for m in prior_months)

    # Compute
    summaries = compute_summaries(monthly, months, prop["units"])
    cap_rate = prop["cap"]
    units = prop["units"]

    # Report date
    today = date.today()
    report_date = today.strftime("%Y.%m.%d")
    report_date_iso = today.strftime("%Y-%m-%d")

    # Generate
    report = generate_full_report(
        code, prop, monthly, summaries, af_totals, months, labels,
        cap_rate, units, report_date, report_date_iso,
        prior_monthly, prior_summaries, prior_t12_noi,
    )

    if args.dry_run:
        print(report)
    else:
        # Determine output path
        quarter = determine_quarter(end_month)
        out_dir = REPORTS_BASE / quarter / prop["fund"]
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{report_date}-{code}-trailing-12m-pnl.md"
        out_path = out_dir / filename
        with open(out_path, "w") as f:
            f.write(report)
        print(f"Wrote: {out_path}", file=sys.stderr)

    # Summary JSON to stdout
    t12_noi = sum(summaries[m]["NOI"] for m in months)
    t12_income = sum(summaries[m]["Total Income"] for m in months)
    t12_opex = sum(summaries[m]["Total OpEx"] for m in months)
    t12_cf = sum(summaries[m]["Cashflow"] for m in months)
    last_dscr = summaries[months[-1]]["DSCR"]

    summary = {
        "property": code,
        "period": f"{months[0]} to {months[-1]}",
        "T12_income": round(t12_income),
        "T12_opex": round(t12_opex),
        "T12_NOI": round(t12_noi),
        "T12_cashflow": round(t12_cf),
        "implied_value": round(t12_noi / cap_rate),
        "DSCR_last_month": round(last_dscr, 2) if last_dscr else None,
        "NOI_per_unit_mo": round(t12_noi / 12 / units),
    }
    if not args.dry_run:
        summary["file"] = str(out_path)

    print(json.dumps(summary))


if __name__ == "__main__":
    main()
