#!/usr/bin/env python3
"""Generate a fund-level P&L summary by aggregating individual property reports.

Runs pnl-fast.py for each property in the fund, parses the generated reports,
and produces a consolidated fund-level P&L with metrics.

Usage:
    python3 portfolio-pnl-fast.py <fund> [--date YYYY-MM] [--skip-existing]

Examples:
    python3 portfolio-pnl-fast.py fund-i
    python3 portfolio-pnl-fast.py fund-iii --date 2026-04
    python3 portfolio-pnl-fast.py fund-i --skip-existing
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

VAULT = Path.home() / "vault"
REPORTS_BASE = VAULT / "m5x2" / "reports"
PNL_SCRIPT = Path(__file__).parent / "pnl-fast.py"

# ---------------------------------------------------------------------------
# Property registry (same as pnl-fast.py)
# ---------------------------------------------------------------------------

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

SREO_VALUES = {
    "a210": 1111, "a511": 974, "a916": 1100, "b101": 1450, "c313": 5570,
    "e328": 825, "h731": 1150, "hl65": 1250, "hl73": 591, "j312": 2174,
    "k104": 8000, "k308": 1220, "kn47": 25400, "l912": 2188, "m221": 1800,
    "m405": 2000, "m608": 1123, "ms22": 875, "ms43": 1850, "o155": 4300,
    "p705": 2700, "ps17": 1600, "ps25": 6300, "ps91": 1650, "rl16": 14000,
    "rl21": 1600, "s129": 2574, "s300": 1350, "tc34": 3300, "tc68": 15800,
    "v202": 2600, "w117": 800, "w225": 900, "w226": 1345,
}

# GL row ordering (must match pnl-fast.py output)
INCOME_ROWS = ["Rent Income", "Concessions", "Utility Reimb", "Late Fees", "Laundry",
               "Pet Rent/Fee", "Parking", "Move-In/Out", "Other Income"]
OPEX_ROWS = ["Prop Mgmt", "Pest Control", "Insurance", "Prop Taxes",
             "R&M Repairs", "R&M Turns", "R&M Grounds", "Electric/Gas",
             "Water", "Garbage", "Other OpEx"]
BELOW_NOI_ROWS = ["Mortgage Interest", "Mortgage Principal", "Legal",
                  "CapEx Turns", "CapEx Appliances", "CapEx Disc", "CapEx Non-disc"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_fund_properties(fund):
    """Return list of property codes belonging to the fund."""
    return sorted([code for code, p in PROPERTIES.items() if p["fund"] == fund])


def period_folder(end_month):
    """Reporting-period folder, e.g. '2026/04-April'. end_month is 'YYYY-MM'
    and already represents the month covered, so the summary lands in the same
    <year>/<NN-Month>/ folder as its property reports. Mirrors pnl-fast.py."""
    names = ["January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]
    y, m = int(end_month[:4]), int(end_month[5:7])
    return f"{y}/{m:02d}-{names[m - 1]}"


def compute_end_month(date_arg):
    """Compute end month from --date arg or default to previous full month."""
    if date_arg:
        return date_arg
    today = date.today()
    if today.month == 1:
        return f"{today.year - 1}-12"
    return f"{today.year}-{today.month - 1:02d}"


def compute_month_labels(end_month):
    """Return (months_list, labels_list) for the trailing 12 months."""
    y, m = int(end_month[:4]), int(end_month[5:7])
    months = []
    for i in range(11, -1, -1):
        mm = m - i
        yy = y
        while mm <= 0:
            mm += 12
            yy -= 1
        months.append(f"{yy:04d}-{mm:02d}")
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


def parse_number(s):
    """Parse a formatted number: strip **, commas, handle (N) as negative."""
    s = s.strip().replace("**", "").replace(",", "").replace("$", "")
    if s in ("", "\u2014", "—"):
        return 0.0
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    try:
        val = float(s)
    except ValueError:
        return 0.0
    return -val if neg else val


# ---------------------------------------------------------------------------
# Report generation for individual properties
# ---------------------------------------------------------------------------

def find_existing_report(code, fund, period, report_date_str):
    """Check if a report for this property with today's date already exists."""
    out_dir = REPORTS_BASE / period / fund
    pattern = f"{report_date_str}-{code}-trailing-12m-pnl.md"
    target = out_dir / pattern
    return target.exists(), target


def run_pnl_fast(code, date_arg=None):
    """Run pnl-fast.py for a property. Returns (success, stdout, stderr)."""
    cmd = [sys.executable, str(PNL_SCRIPT), code]
    if date_arg:
        cmd += ["--date", date_arg]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Timeout after 60s"


# ---------------------------------------------------------------------------
# Report parsing
# ---------------------------------------------------------------------------

def find_latest_report(code, fund, period):
    """Find the most recent report file for a property."""
    out_dir = REPORTS_BASE / period / fund
    if not out_dir.exists():
        return None
    # Look for files matching the pattern, get most recent by name sort
    candidates = sorted(out_dir.glob(f"*-{code}-trailing-12m-pnl.md"), reverse=True)
    return candidates[0] if candidates else None


def parse_report_pnl(filepath):
    """Parse the main P&L table from a property report.

    Returns:
        dict: {label: [val_m1, val_m2, ..., val_m12]} for each GL line
        dict: {label: t12_value} for summary rows
        float: last month DSCR (from Derived Metrics)
        float: T-12 cashflow
    """
    if not filepath or not filepath.exists():
        return None, None, None, None

    with open(filepath) as f:
        lines = f.readlines()

    # Parse the main P&L table
    gl_data = {}  # label -> [12 monthly values]
    summary_data = {}  # label -> T12 value
    in_table = False
    header_seen = False

    for line in lines:
        line = line.rstrip()
        if not line.startswith("|"):
            if in_table and header_seen:
                # End of table
                break
            continue

        cells = [c.strip() for c in line.split("|")]
        # cells[0] and cells[-1] are empty from leading/trailing |
        cells = cells[1:-1]

        if not cells:
            continue

        # Detect header row
        if cells[0].strip().replace("*", "") == "Account":
            in_table = True
            header_seen = True
            continue

        # Skip separator row
        if cells[0].startswith(":--") or cells[0].startswith("--"):
            continue

        if not in_table:
            continue

        label = cells[0].strip().replace("**", "").strip()

        # Skip blank rows
        if not label:
            continue

        # Skip Derived Metrics and beyond
        if label.startswith("Derived") or label == "Metric":
            break

        # We expect 13 value columns (12 months + T12)
        if len(cells) < 14:
            continue

        monthly_vals = [parse_number(cells[i]) for i in range(1, 13)]
        t12_val = parse_number(cells[13])

        gl_data[label] = monthly_vals
        summary_data[label] = t12_val

    # Parse DSCR from Derived Metrics
    dscr = None
    for line in lines:
        if "| DSCR |" in line:
            cells = [c.strip() for c in line.split("|")]
            cells = cells[1:-1]
            if len(cells) >= 13:
                # Last monthly column (index 12) is the last month's DSCR
                dscr_str = cells[12].strip().replace("x", "").replace("**", "")
                try:
                    dscr = float(dscr_str)
                except ValueError:
                    dscr = None
            break

    # Parse the Equity section (separate 2-column Metric/Value table below the
    # P&L). Stash into summary_data under reserved keys so the return signature
    # stays put. Debt and equity are computed in pnl-fast and only surfaced here.
    in_equity = False
    for line in lines:
        line = line.rstrip()
        if line.startswith("## Equity"):
            in_equity = True
            continue
        if in_equity:
            if line.startswith("##"):
                break
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")][1:-1]
            if len(cells) < 2:
                continue
            label = cells[0].replace("**", "").strip()
            if label in ("Outstanding Debt", "Equity"):
                val_cell = cells[1].replace("**", "").strip()
                # Em-dash means "not computed" (no debt on file) — keep it None
                # rather than letting parse_number collapse it to a real 0.
                summary_data[f"_{label}"] = None if val_cell in ("", "—", "—") else parse_number(cells[1])

    t12_cashflow = summary_data.get("Cashflow", 0)
    return gl_data, summary_data, dscr, t12_cashflow


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_fund_data(prop_reports):
    """Aggregate GL data across all properties.

    Args:
        prop_reports: dict of {code: (gl_data, summary_data, dscr, t12_cf)}

    Returns:
        fund_gl: {label: [sum_m1, ..., sum_m12]}
        prop_metrics: {code: {noi, cashflow, dscr, ...}}
    """
    fund_gl = defaultdict(lambda: [0.0] * 12)
    prop_metrics = {}

    for code, (gl_data, summary_data, dscr, t12_cf) in prop_reports.items():
        if gl_data is None:
            continue

        for label, vals in gl_data.items():
            for i in range(12):
                fund_gl[label][i] += vals[i]

        t12_noi = summary_data.get("NOI", 0)
        t12_income = summary_data.get("Total Income", 0)
        units = PROPERTIES[code]["units"]
        cap = PROPERTIES[code].get("cap")
        noi_unit_mo = t12_noi / 12 / units if units > 0 else 0

        prop_metrics[code] = {
            "t12_noi": t12_noi,
            "t12_cashflow": t12_cf,
            "dscr": dscr,
            "cap": cap,
            "units": units,
            "noi_unit_mo": noi_unit_mo,
            "sreo": SREO_VALUES.get(code, 0),
            "implied_value": round(t12_noi / cap) if cap and cap > 0 else 0,
            "debt": summary_data.get("_Outstanding Debt"),
            "equity": summary_data.get("_Equity"),
            # Derived here (not parsed) so it's robust to older reports that
            # predate the Net Realizable Equity row: Equity − 9% of SREO Value.
            "realizable_equity": (summary_data["_Equity"] - SREO_VALUES.get(code, 0) * 1000 * 0.09)
                                 if summary_data.get("_Equity") is not None else None,
        }

    return dict(fund_gl), prop_metrics


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt(val, bold=False):
    if val is None:
        return "\u2014"
    r = round(val)
    s = f"({abs(r):,})" if r < 0 else f"{r:,}"
    return f"**{s}**" if bold else s


def fmt_k(val):
    if val is None or val == 0:
        return "\u2014"
    r = round(val / 1000)
    return f"{r:,}K"


# ---------------------------------------------------------------------------
# Report building
# ---------------------------------------------------------------------------

def build_fund_report(fund, fund_gl, prop_metrics, months, labels, report_date, report_date_iso):
    """Build the complete fund summary markdown report."""
    props = sorted(prop_metrics.keys(), key=lambda c: prop_metrics[c]["t12_noi"], reverse=True)
    total_units = sum(prop_metrics[c]["units"] for c in props)
    total_noi = sum(prop_metrics[c]["t12_noi"] for c in props)
    total_cf = sum(prop_metrics[c]["t12_cashflow"] for c in props)
    total_sreo = sum(prop_metrics[c]["sreo"] for c in props)

    # Value-weighted cap rate
    weighted_num = sum(prop_metrics[c]["sreo"] * (prop_metrics[c]["cap"] or 0) for c in props if prop_metrics[c]["cap"])
    weighted_den = sum(prop_metrics[c]["sreo"] for c in props if prop_metrics[c]["cap"])
    weighted_cap = weighted_num / weighted_den if weighted_den > 0 else 0
    weighted_cap_pct = f"{weighted_cap * 100:.2f}%"

    implied_value_fund = round(total_noi / weighted_cap) if weighted_cap > 0 else 0
    noi_unit_mo = round(total_noi / 12 / total_units) if total_units > 0 else 0

    # Period label
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    month_names_full = ["January", "February", "March", "April", "May", "June",
                        "July", "August", "September", "October", "November", "December"]
    fy1, fm1 = int(months[0][:4]), int(months[0][5:7])
    fy2, fm2 = int(months[-1][:4]), int(months[-1][5:7])
    period_short = f"{month_names_full[fm1-1]} {fy1} - {month_names_full[fm2-1]} {fy2}"
    period_label = f"({month_names[fm1-1]} {fy1} - {month_names[fm2-1]} {fy2})"

    fund_title = fund.replace("-", " ").title().replace(" ", " ")
    # Capitalize Roman numerals
    fund_title = fund_title.replace("Fund 0", "Fund 0").replace("Fund Iv", "Fund IV").replace("Fund Iii", "Fund III").replace("Fund Ii", "Fund II").replace("Fund I", "Fund I")

    r = []

    # Frontmatter
    r.append("---")
    r.append(f'title: "{fund_title} Portfolio Summary"')
    r.append(f"date: {report_date_iso}")
    r.append("type: report")
    r.append(f"tags: [m5x2, {fund}, pnl, summary]")
    r.append("source: appfolio")
    r.append("---")
    r.append("")

    # Portfolio summary
    r.append(f"## {fund_title} Portfolio Summary {period_label}")
    r.append("")
    r.append(f"{len(props)} properties, {total_units} units | Value-Weighted Cap Rate: {weighted_cap_pct}")
    r.append("")
    r.append("| Metric | Value |")
    r.append("|--------|------:|")
    r.append(f"| T-12 NOI | ${total_noi:,.0f} |")
    r.append(f"| T-12 Cashflow | ${total_cf:,.0f} |")
    r.append(f"| SREO Value | ${total_sreo:,}K |")
    r.append(f"| Implied Value ({weighted_cap_pct} cap) | ${fmt_k(implied_value_fund)} |")
    r.append(f"| NOI/Unit/Mo | ${noi_unit_mo:,} |")
    r.append("")

    # Property breakdown
    r.append("## Property Breakdown")
    r.append("")
    r.append("| Property | Units | T-12 NOI | Cashflow | DSCR | Cap | SREO Value | NOI/U/Mo | Implied Value |")
    r.append("|----------|------:|---------:|---------:|-----:|----:|-----------:|---------:|--------------:|")
    for code in props:
        m = prop_metrics[code]
        dscr_s = f"{m['dscr']:.2f}" if m['dscr'] else "\u2014"
        cap_s = f"{m['cap']*100:.1f}%" if m['cap'] else "\u2014"
        cf_s = f"({abs(round(m['t12_cashflow'])):,})" if m['t12_cashflow'] < 0 else f"{round(m['t12_cashflow']):,}"
        r.append(f"| {code} | {m['units']} | {round(m['t12_noi']):,} | {cf_s} | {dscr_s} | {cap_s} | {m['sreo']:,}K | {round(m['noi_unit_mo']):,} | {fmt_k(m['implied_value'])} |")

    # Total row
    total_implied_k = fmt_k(implied_value_fund)
    total_cf_s = f"({abs(round(total_cf)):,})" if total_cf < 0 else f"{round(total_cf):,}"
    r.append(f"| **Total** | **{total_units}** | **{round(total_noi):,}** | **{total_cf_s}** | | **{weighted_cap_pct}** | **{total_sreo:,}K** | **{noi_unit_mo:,}** | **{total_implied_k}** |")
    r.append("")

    # Value-Weighted Cap Rate Calculation
    r.append("## Value-Weighted Cap Rate Calculation")
    r.append("")
    r.append("Cap rate = sum(SREO Value x Property Cap Rate) / sum(SREO Value)")
    r.append("")
    r.append("| Property | SREO Value | Cap Rate | Weighted Contribution |")
    r.append("|----------|----------:|--------:|---------------------:|")
    total_weighted = 0
    for code in sorted(props, key=lambda c: prop_metrics[c]["sreo"], reverse=True):
        m = prop_metrics[code]
        if m["cap"] and m["sreo"]:
            contrib = m["sreo"] * m["cap"]
            total_weighted += contrib
            r.append(f"| {code} | {m['sreo']:,}K | {m['cap']*100:.1f}% | {round(contrib):,}K |")
    r.append(f"| **Total** | **{total_sreo:,}K** | | **{round(total_weighted):,}K** |")
    r.append("")
    r.append(f"Weighted Cap = {round(total_weighted):,}K / {total_sreo:,}K = **{weighted_cap_pct}**")
    r.append("")

    # SREO vs Implied Value
    r.append("## SREO vs Implied Value")
    r.append("")
    r.append("| Property | SREO Value | Implied Value | Gap | Gap % |")
    r.append("|----------|----------:|--------------:|----:|------:|")
    total_implied_sum = 0
    total_gap = 0
    for code in sorted(props, key=lambda c: prop_metrics[c]["sreo"], reverse=True):
        m = prop_metrics[code]
        sreo = m["sreo"]
        implied_k = round(m["implied_value"] / 1000) if m["implied_value"] else 0
        total_implied_sum += implied_k
        gap = implied_k - sreo
        total_gap += gap
        gap_pct = (gap / sreo * 100) if sreo > 0 else 0
        gap_s = f"+{gap:,}K" if gap >= 0 else f"({abs(gap):,}K)"
        gap_pct_s = f"+{gap_pct:.1f}%" if gap_pct >= 0 else f"-{abs(gap_pct):.1f}%"
        r.append(f"| {code} | {sreo:,}K | {implied_k:,}K | {gap_s} | {gap_pct_s} |")
    total_gap_s = f"+{total_gap:,}K" if total_gap >= 0 else f"({abs(total_gap):,}K)"
    total_gap_pct = (total_gap / total_sreo * 100) if total_sreo > 0 else 0
    total_gap_pct_s = f"+{total_gap_pct:.1f}%" if total_gap_pct >= 0 else f"-{abs(total_gap_pct):.1f}%"
    r.append(f"| **Total** | **{total_sreo:,}K** | **{total_implied_sum:,}K** | **{total_gap_s}** | **{total_gap_pct_s}** |")
    r.append("")

    # Equity (rolled up from each property's Equity section)
    eq_props = [c for c in props if prop_metrics[c].get("equity") is not None]
    if eq_props:
        t_sreo_eq = sum(prop_metrics[c]["sreo"] * 1000 for c in eq_props)
        t_debt = sum(prop_metrics[c]["debt"] for c in eq_props)
        t_equity = sum(prop_metrics[c]["equity"] for c in eq_props)
        t_sale_cost = t_sreo_eq * 0.09
        t_realizable = sum(prop_metrics[c]["realizable_equity"] for c in eq_props)
        r.append("## Equity")
        r.append("")
        r.append("| Property | SREO Value | Debt | Equity | Sale Cost (9%) | Net Realizable Equity |")
        r.append("|----------|----------:|-----:|-------:|---------------:|----------------------:|")
        for code in sorted(eq_props, key=lambda c: prop_metrics[c]["realizable_equity"], reverse=True):
            m = prop_metrics[code]
            sale_cost = m["sreo"] * 1000 * 0.09
            r.append(f"| {code} | {fmt_k(m['sreo']*1000)} | {fmt_k(m['debt'])} | "
                     f"{fmt_k(m['equity'])} | {fmt_k(sale_cost)} | {fmt_k(m['realizable_equity'])} |")
        ltv = t_debt / t_sreo_eq * 100 if t_sreo_eq else 0
        eq_pct = t_equity / t_sreo_eq * 100 if t_sreo_eq else 0
        r.append(f"| **Total** | **{fmt_k(t_sreo_eq)}** | **{fmt_k(t_debt)}** | "
                 f"**{fmt_k(t_equity)}** | **{fmt_k(t_sale_cost)}** | **{fmt_k(t_realizable)}** |")
        r.append("")
        r.append(f"LTV = {ltv:.1f}% | Equity % = {eq_pct:.1f}%. Net Realizable Equity = "
                 "Equity − sale cost (9% of SREO Value), the net cash from a disposition.")
        missing = [c for c in props if prop_metrics[c].get("equity") is None]
        if missing:
            r.append("")
            r.append(f"_Excludes {', '.join(sorted(missing))} (no debt on file; equity not computed)._")
        r.append("")

    # Cashflow Negative Properties
    neg_cf_props = [(c, prop_metrics[c]) for c in props if prop_metrics[c]["t12_cashflow"] < 0]
    if neg_cf_props:
        neg_cf_props.sort(key=lambda x: x[1]["t12_cashflow"])
        r.append("## Cashflow Negative Properties")
        r.append("")
        r.append("| Property | T-12 Cashflow | Primary Driver |")
        r.append("|----------|-------------:|----------------|")
        combined_neg = 0
        for code, m in neg_cf_props:
            cf_val = round(m["t12_cashflow"])
            combined_neg += cf_val
            noi_u = round(m["noi_unit_mo"])
            dscr_s = f"{m['dscr']:.2f}" if m['dscr'] else "n/a"
            r.append(f"| {code} | ({abs(cf_val):,}) | DSCR {dscr_s}, NOI/unit/mo ${noi_u:,} |")
        erode_pct = abs(combined_neg) / total_cf * 100 if total_cf > 0 else 0
        r.append("")
        r.append(f"Combined negative cashflow drag: $({abs(round(combined_neg)):,}), erasing {erode_pct:.1f}% of fund-level cashflow.")
        r.append("")

    # Consolidated P&L
    r.append(f"## {fund_title} Consolidated P&L {period_label}")
    r.append("")
    r.append(build_consolidated_pnl(fund_gl, months, labels))

    # Fund P&L Observations
    r.append("### Fund P&L Observations")
    r.append("")
    r.append(build_observations(fund_gl, months, total_noi, total_cf, props, prop_metrics))
    r.append("")

    r.append(f"Generated from AppFolio on {report_date}.")

    return "\n".join(r) + "\n"


def build_consolidated_pnl(fund_gl, months, labels):
    """Build the consolidated P&L table."""
    hdr = "| Account |" + "".join(f" {l} |" for l in labels) + " **T12** |"
    sep = "|:--------|" + " -------:|" * len(labels) + " -------:|"
    lines = [hdr, sep]

    def add_row(label, vals, bold=False, force=False):
        if not force and all(v == 0 for v in vals):
            return
        row = f"| {'**' + label + '**' if bold else label} |"
        t12 = sum(vals)
        for v in vals:
            row += f" {fmt(v, bold)} |"
        row += f" {fmt(t12, bold)} |"
        lines.append(row)

    def blank():
        lines.append("|" + " |" * (len(labels) + 2))

    # Compute summaries from fund_gl
    def get_vals(label):
        return fund_gl.get(label, [0.0] * 12)

    def sum_group(group_labels):
        result = [0.0] * 12
        for label in group_labels:
            vals = get_vals(label)
            for i in range(12):
                result[i] += vals[i]
        return result

    # Income section
    lines.append("| **Income** |" + " |" * (len(labels) + 1))
    for label in INCOME_ROWS:
        add_row(label, get_vals(label))
    total_income = sum_group(INCOME_ROWS)
    add_row("Total Income", total_income, bold=True, force=True)
    blank()

    # Operating Expenses
    lines.append("| **Operating Expenses** |" + " |" * (len(labels) + 1))
    for label in OPEX_ROWS:
        add_row(label, get_vals(label))
    total_opex = sum_group(OPEX_ROWS)
    add_row("Total OpEx", total_opex, bold=True, force=True)
    blank()

    # NOI
    noi = [total_income[i] - total_opex[i] for i in range(12)]
    add_row("NOI", noi, bold=True, force=True)
    blank()

    # Below-NOI Deductions
    lines.append("| **Below-NOI Deductions** |" + " |" * (len(labels) + 1))
    for label in BELOW_NOI_ROWS:
        add_row(label, get_vals(label))
    total_ded = sum_group(BELOW_NOI_ROWS)
    add_row("Total Deductions", total_ded, bold=True, force=True)
    blank()

    # Cashflow
    cashflow = [noi[i] - total_ded[i] for i in range(12)]
    add_row("Cashflow", cashflow, bold=True, force=True)

    return "\n".join(lines) + "\n"


def build_observations(fund_gl, months, total_noi, total_cf, props, prop_metrics):
    """Auto-generate fund-level P&L observations."""
    obs = []

    # R&M trend
    rm_labels = ["R&M Repairs", "R&M Turns", "R&M Grounds"]
    rm_monthly = [sum(fund_gl.get(l, [0]*12)[i] for l in rm_labels) for i in range(12)]
    total_income_vals = [0.0] * 12
    for label in INCOME_ROWS:
        vals = fund_gl.get(label, [0.0] * 12)
        for i in range(12):
            total_income_vals[i] += vals[i]
    t12_income = sum(total_income_vals)
    t12_rm = sum(rm_monthly)
    rm_pct = t12_rm / t12_income * 100 if t12_income else 0
    max_rm = max(rm_monthly)
    min_rm = min(rm_monthly)
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    max_rm_idx = rm_monthly.index(max_rm)
    min_rm_idx = rm_monthly.index(min_rm)
    obs.append(f"- **R&M (Repairs + Turns + Grounds) = ${t12_rm:,.0f}, or {rm_pct:.1f}% of income.** Peak ${max_rm:,.0f}/mo, trough ${min_rm:,.0f}/mo.")

    # Income trend
    first_income = total_income_vals[0]
    last_income = total_income_vals[-1]
    if first_income > 0:
        income_growth = (last_income - first_income) / first_income * 100
        obs.append(f"- **Income trending {'up' if income_growth > 0 else 'down'}.** Total Income moved from ${first_income:,.0f} to ${last_income:,.0f}, a {abs(income_growth):.1f}% {'increase' if income_growth > 0 else 'decrease'}.")

    # Insurance
    ins_vals = fund_gl.get("Insurance", [0]*12)
    t12_ins = sum(ins_vals)
    ins_pct = t12_ins / t12_income * 100 if t12_income else 0
    obs.append(f"- **T-12 insurance = ${t12_ins:,.0f} ({ins_pct:.1f}% of income).**")

    # CapEx
    capex_labels = ["CapEx Turns", "CapEx Appliances", "CapEx Disc", "CapEx Non-disc"]
    t12_capex = sum(sum(fund_gl.get(l, [0]*12)) for l in capex_labels)
    capex_pct = t12_capex / total_noi * 100 if total_noi else 0
    obs.append(f"- **Total T-12 CapEx = ${t12_capex:,.0f}**, consuming {capex_pct:.1f}% of NOI.")

    # Debt service ratio
    t12_mortgage_int = sum(fund_gl.get("Mortgage Interest", [0]*12))
    t12_mortgage_princ = sum(fund_gl.get("Mortgage Principal", [0]*12))
    t12_debt = t12_mortgage_int + t12_mortgage_princ
    debt_pct = t12_debt / total_noi * 100 if total_noi else 0
    fund_dscr = total_noi / t12_debt if t12_debt else None
    dscr_s = f"{fund_dscr:.2f}" if fund_dscr else "n/a"
    obs.append(f"- **Debt service = ${t12_debt:,.0f}** ({debt_pct:.0f}% of NOI). Fund-level DSCR = {dscr_s}.")

    return "\n".join(obs)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate fund-level P&L summary")
    parser.add_argument("fund", help="Fund name (e.g. fund-i, fund-ii, fund-iii)")
    parser.add_argument("--date", help="End month YYYY-MM (default: previous full month)", default=None)
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip regeneration if report dated today already exists")
    parser.add_argument("--parse-only", action="store_true",
                        help="Skip all regeneration, only parse existing reports")
    args = parser.parse_args()
    if args.parse_only:
        args.skip_existing = True

    fund = args.fund.lower()
    fund_props = get_fund_properties(fund)
    if not fund_props:
        print(json.dumps({"error": f"No properties found for fund: {fund}",
                          "valid_funds": sorted(set(p["fund"] for p in PROPERTIES.values()))}))
        sys.exit(1)

    end_month = compute_end_month(args.date)
    period = period_folder(end_month)
    months, labels = compute_month_labels(end_month)

    today = date.today()
    report_date = today.strftime("%Y.%m.%d")
    report_date_iso = today.strftime("%Y-%m-%d")
    report_date_file = today.strftime("%Y.%m.%d")

    print(f"Generating {fund} portfolio P&L ({len(fund_props)} properties)...", file=sys.stderr)
    print(f"Period: {months[0]} to {months[-1]}", file=sys.stderr)

    # Step 1: Generate/refresh individual property reports
    if args.parse_only:
        print("  --parse-only: skipping report generation", file=sys.stderr)
    for i, code in enumerate(fund_props):
        if args.parse_only:
            continue
        prop = PROPERTIES[code]

        if args.skip_existing:
            exists, path = find_existing_report(code, fund, period, report_date_file)
            if exists:
                print(f"  [{i+1}/{len(fund_props)}] {code}: report exists, skipping", file=sys.stderr)
                continue

        print(f"  [{i+1}/{len(fund_props)}] {code}: generating report...", file=sys.stderr)

        success, stdout, stderr = run_pnl_fast(code, args.date)
        if not success:
            print(f"    ERROR: {stderr.strip()}", file=sys.stderr)
            # Wait and retry once
            print(f"    Retrying in 30s...", file=sys.stderr)
            time.sleep(30)
            success, stdout, stderr = run_pnl_fast(code, args.date)
            if not success:
                print(f"    FAILED after retry: {stderr.strip()}", file=sys.stderr)
                continue

        if stderr:
            for line in stderr.strip().split("\n"):
                if line:
                    print(f"    {line}", file=sys.stderr)

        # Rate limiting: 5s between API calls
        if i < len(fund_props) - 1:
            time.sleep(5)

    # Step 2: Parse all property reports
    prop_reports = {}
    for code in fund_props:
        report_path = find_latest_report(code, fund, period)
        gl_data, summary_data, dscr, t12_cf = parse_report_pnl(report_path)
        if gl_data is not None:
            prop_reports[code] = (gl_data, summary_data, dscr, t12_cf)
            print(f"  Parsed {code}: NOI={summary_data.get('NOI', 0):,.0f}", file=sys.stderr)
        else:
            print(f"  WARNING: Could not parse report for {code}", file=sys.stderr)

    if not prop_reports:
        print(json.dumps({"error": "No property reports could be parsed"}))
        sys.exit(1)

    # Step 3: Aggregate
    fund_gl, prop_metrics = aggregate_fund_data(prop_reports)

    # Step 4: Build report
    report = build_fund_report(fund, fund_gl, prop_metrics, months, labels, report_date, report_date_iso)

    # Step 5: Write
    out_dir = REPORTS_BASE / period / fund
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{report_date_file}-{fund}-summary.md"
    out_path = out_dir / filename
    with open(out_path, "w") as f:
        f.write(report)
    print(f"\nWrote: {out_path}", file=sys.stderr)

    # Step 6: Summary JSON to stdout
    total_noi = sum(prop_metrics[c]["t12_noi"] for c in prop_metrics)
    total_cf = sum(prop_metrics[c]["t12_cashflow"] for c in prop_metrics)
    total_units = sum(prop_metrics[c]["units"] for c in prop_metrics)
    total_sreo = sum(prop_metrics[c]["sreo"] for c in prop_metrics)

    weighted_num = sum(prop_metrics[c]["sreo"] * (prop_metrics[c]["cap"] or 0) for c in prop_metrics if prop_metrics[c]["cap"])
    weighted_den = sum(prop_metrics[c]["sreo"] for c in prop_metrics if prop_metrics[c]["cap"])
    weighted_cap = weighted_num / weighted_den if weighted_den > 0 else 0

    summary = {
        "fund": fund,
        "properties": len(prop_metrics),
        "units": total_units,
        "period": f"{months[0]} to {months[-1]}",
        "T12_NOI": round(total_noi),
        "T12_cashflow": round(total_cf),
        "SREO_value_K": total_sreo,
        "implied_value_K": round(total_noi / weighted_cap / 1000) if weighted_cap > 0 else None,
        "weighted_cap_rate": round(weighted_cap * 100, 2),
        "NOI_per_unit_mo": round(total_noi / 12 / total_units) if total_units > 0 else 0,
        "fund_DSCR": None,
        "equity": round(sum(prop_metrics[c]["equity"] for c in prop_metrics
                            if prop_metrics[c].get("equity") is not None)),
        "realizable_equity": round(sum(prop_metrics[c]["realizable_equity"] for c in prop_metrics
                                       if prop_metrics[c].get("realizable_equity") is not None)),
        "file": str(out_path),
    }

    # Compute fund DSCR
    t12_debt = sum(fund_gl.get("Mortgage Interest", [0]*12)) + sum(fund_gl.get("Mortgage Principal", [0]*12))
    if t12_debt > 0:
        summary["fund_DSCR"] = round(total_noi / t12_debt, 2)

    print(json.dumps(summary))


if __name__ == "__main__":
    main()
