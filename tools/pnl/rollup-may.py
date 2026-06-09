#!/usr/bin/env python3
"""Roll up all five fund summaries in 2026/05-May into one portfolio-wide doc.

Reads fund-level metrics from each portfolio script's JSON, the per-property
NOI-vs-budget (with z / flag) tables, and sums the consolidated P&Ls into a
single portfolio income statement. Pure aggregation — no new AppFolio calls
beyond what --skip-existing already produced.
"""
import json
import re
import subprocess
from pathlib import Path

BASE = Path.home() / "vault/m5x2/reports/2026/05-May"
TOOL = Path.home() / "i446-monorepo/tools/pnl/portfolio-pnl-fast.py"
FUNDS = ["fund-0", "fund-i", "fund-ii", "fund-iii", "fund-iv"]
FUND_LABEL = {"fund-0": "Fund 0", "fund-i": "Fund I", "fund-ii": "Fund II",
              "fund-iii": "Fund III", "fund-iv": "Fund IV"}


def parse_num(s):
    s = s.strip().replace("**", "").replace(",", "").replace("$", "").replace("K", "")
    if s in ("", "—", "-", "–"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def fund_summary_path(fund):
    return BASE / fund / f"2026.06.09-{fund}-summary.md"


def get_meta(fund):
    """Run the portfolio script (reusing today's reports) to capture clean JSON."""
    out = subprocess.run(["python3", str(TOOL), fund, "--skip-existing"],
                         capture_output=True, text=True)
    js = [l for l in out.stdout.splitlines() if l.strip().startswith("{")][-1]
    return json.loads(js)


def parse_noi_budget(fund):
    """Return list of dicts for each property row in the NOI vs Budget table."""
    rows = []
    in_seg = False
    for ln in fund_summary_path(fund).read_text().splitlines():
        if ln.startswith("## NOI vs Budget"):
            in_seg = True
            continue
        if in_seg and ln.startswith("## "):
            break
        if in_seg and ln.lstrip().startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) < 8:
                continue
            code = cells[0].replace("**", "")
            if code in ("Property", "Total") or code.startswith(":--") or code.startswith("--"):
                continue
            rows.append({
                "code": code, "fund": fund, "units": cells[1], "month": cells[2],
                "actual": parse_num(cells[3]), "budget": parse_num(cells[4]),
                "pct": cells[5], "z": parse_num(cells[6]), "flag": cells[7],
            })
    return rows


def parse_consolidated_pnl(fund):
    """Return {label: [12 monthly + T12]} from the fund consolidated P&L section."""
    data = {}
    in_seg = False
    for ln in fund_summary_path(fund).read_text().splitlines():
        if re.match(r"## .*Consolidated P&L", ln):
            in_seg = True
            continue
        if in_seg and ln.startswith("### "):
            break
        if in_seg and ln.lstrip().startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            label = cells[0].replace("**", "").strip()
            if not label or label == "Account" or label.startswith(":--"):
                continue
            nums = [parse_num(c) for c in cells[1:]]
            if len(nums) >= 13 and any(n is not None for n in nums[:13]):
                data[label] = [n or 0.0 for n in nums[:13]]
    return data


PNL_ORDER = [
    ("Income", ["Rent Income", "Concessions", "Utility Reimb", "Late Fees", "Laundry",
                "Pet Rent/Fee", "Parking", "Move-In/Out", "Other Income"]),
    ("Total Income", None),
    ("Operating Expenses", ["Prop Mgmt", "Pest Control", "Insurance", "Prop Taxes",
                            "R&M Repairs", "R&M Turns", "R&M Grounds", "Electric/Gas",
                            "Water", "Garbage", "Other OpEx"]),
    ("Total OpEx", None),
    ("NOI", None),
    ("Below-NOI Deductions", ["Mortgage Interest", "Mortgage Principal", "Legal",
                              "CapEx Turns", "CapEx Appliances", "CapEx Disc",
                              "CapEx Non-disc"]),
    ("Total Deductions", None),
    ("Cashflow", None),
]
MONTHS = ["Jun 25", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
          "Jan 26", "Feb", "Mar", "Apr", "May", "**T12**"]


def fmt(v):
    if v is None:
        return "—"
    v = round(v)
    return f"({abs(v):,})" if v < 0 else f"{v:,}"


def main():
    meta = {f: get_meta(f) for f in FUNDS}
    noi_rows = []
    for f in FUNDS:
        noi_rows += parse_noi_budget(f)

    # Sum consolidated P&Ls across funds, label by label.
    pnls = {f: parse_consolidated_pnl(f) for f in FUNDS}
    combined = {}
    for f in FUNDS:
        for label, vals in pnls[f].items():
            acc = combined.setdefault(label, [0.0] * 13)
            for i in range(13):
                acc[i] += vals[i]

    # ---- Totals ----
    tot = {k: 0.0 for k in ("props", "units", "noi", "cf", "sreo_k", "impl_k",
                            "equity", "realiz")}
    for f in FUNDS:
        m = meta[f]
        tot["props"] += m["properties"]
        tot["units"] += m["units"]
        tot["noi"] += m["T12_NOI"]
        tot["cf"] += m["T12_cashflow"]
        tot["sreo_k"] += m["SREO_value_K"]
        tot["impl_k"] += m["implied_value_K"]
        tot["equity"] += m["equity"]
        tot["realiz"] += m["realizable_equity"]
    debt = tot["sreo_k"] * 1000 - tot["equity"]
    wcap = sum(meta[f]["SREO_value_K"] * meta[f]["weighted_cap_rate"] for f in FUNDS) / tot["sreo_k"]
    noi_unit_mo = tot["noi"] / tot["units"] / 12

    out = []
    out.append("---")
    out.append('title: "All-Funds May 2026 Portfolio Roll-Up"')
    out.append("date: 2026-06-09")
    out.append("type: report")
    out.append("tags: [m5x2, pnl, summary, rollup, all-funds]")
    out.append("source: appfolio")
    out.append("---")
    out.append("")
    out.append("## All-Funds Portfolio Roll-Up (Jun 2025 – May 2026)")
    out.append("")
    out.append(f"**{int(tot['props'])} properties · {int(tot['units'])} units · 5 funds** "
               f"| Value-Weighted Cap Rate: {wcap*100:.2f}%")
    out.append("")
    out.append("| Metric | Value |")
    out.append("|--------|------:|")
    out.append(f"| T-12 NOI | ${fmt(tot['noi'])} |")
    out.append(f"| T-12 Cashflow | ${fmt(tot['cf'])} |")
    out.append(f"| SREO Value | ${fmt(tot['sreo_k'])}K |")
    out.append(f"| Implied Value ({wcap*100:.2f}% cap) | ${fmt(tot['impl_k'])}K |")
    out.append(f"| NOI/Unit/Mo | ${fmt(noi_unit_mo)} |")
    ds = combined.get("Mortgage Interest", [0]*13)[12] + combined.get("Mortgage Principal", [0]*13)[12]
    out.append(f"| Portfolio DSCR | {tot['noi']/ds:.2f} |")
    out.append(f"| Equity | ${fmt(tot['equity'])} |")
    out.append(f"| Net Realizable Equity | ${fmt(tot['realiz'])} |")
    out.append("")

    # ---- Fund breakdown ----
    out.append("## Fund Breakdown")
    out.append("")
    out.append("| Fund | Props | Units | T-12 NOI | Cashflow | DSCR | Cap | SREO | Implied | Equity | Net Realiz. |")
    out.append("|------|------:|------:|---------:|---------:|-----:|----:|-----:|--------:|-------:|------------:|")
    for f in FUNDS:
        m = meta[f]
        out.append(f"| {FUND_LABEL[f]} | {m['properties']} | {m['units']} | "
                   f"{fmt(m['T12_NOI'])} | {fmt(m['T12_cashflow'])} | {m.get('fund_DSCR','—')} | "
                   f"{m['weighted_cap_rate']:.2f}% | {fmt(m['SREO_value_K'])}K | {fmt(m['implied_value_K'])}K | "
                   f"{fmt(m['equity'])} | {fmt(m['realizable_equity'])} |")
    out.append(f"| **Total** | **{int(tot['props'])}** | **{int(tot['units'])}** | "
               f"**{fmt(tot['noi'])}** | **{fmt(tot['cf'])}** | **{tot['noi']/ds:.2f}** | "
               f"**{wcap*100:.2f}%** | **{fmt(tot['sreo_k'])}K** | **{fmt(tot['impl_k'])}K** | "
               f"**{fmt(tot['equity'])}** | **{fmt(tot['realiz'])}** |")
    out.append("")

    # ---- NOI vs Budget, all properties, worst z first ----
    out.append("## NOI vs Budget — All Properties (May, worst z first)")
    out.append("")
    out.append("| Property | Fund | Units | Actual NOI | Budget NOI | Δ vs Budget | z | Flag |")
    out.append("|----------|------|------:|-----------:|-----------:|------------:|----:|:----:|")
    flagged = [r for r in noi_rows if r["z"] is not None]
    unflagged = [r for r in noi_rows if r["z"] is None]
    flagged.sort(key=lambda r: r["z"])
    counts = {"🟢": 0, "🟡": 0, "🔴": 0}
    sa = sb = 0.0
    for r in flagged + unflagged:
        z_s = f"{r['z']:+.1f}" if r["z"] is not None else "—"
        out.append(f"| {r['code']} | {FUND_LABEL[r['fund']]} | {r['units']} | "
                   f"{fmt(r['actual'])} | {fmt(r['budget'])} | {r['pct']} | {z_s} | {r['flag']} |")
        if r["flag"] in counts:
            counts[r["flag"]] += 1
        if r["budget"]:
            sa += r["actual"]
            sb += r["budget"]
    tot_pct = (sa - sb) / sb * 100
    out.append(f"| **Total (budgeted)** | | | **{fmt(sa)}** | **{fmt(sb)}** | "
               f"**{tot_pct:+.0f}%** | | |")
    out.append("")
    out.append(f"Flag = size-adjusted significance of the miss (z = Δ% ÷ √(p(1−p)/n), p=0.93): "
               f"🟢 z>−1 (noise) · 🟡 −2<z≤−1 (watch) · 🔴 z≤−2 (real problem). "
               f"**Portfolio: 🟢 {counts['🟢']} · 🟡 {counts['🟡']} · 🔴 {counts['🔴']}.**")
    reds = [r["code"] for r in flagged if r["flag"] == "🔴"]
    if reds:
        out.append("")
        out.append(f"**Red flags ({len(reds)}):** " + ", ".join(
            f"{r['code']} ({FUND_LABEL[r['fund']]}, {r['units']}u, {r['pct']}, z {r['z']:+.1f})"
            for r in flagged if r["flag"] == "🔴"))
    out.append("")

    # ---- Equity roll-up ----
    out.append("## Equity")
    out.append("")
    out.append("| Metric | Value |")
    out.append("|--------|------:|")
    out.append(f"| SREO Value | ${fmt(tot['sreo_k'])}K |")
    out.append(f"| Outstanding Debt | ${fmt(debt)} |")
    out.append(f"| **Equity** | **${fmt(tot['equity'])}** |")
    out.append(f"| Net Realizable Equity | ${fmt(tot['realiz'])} |")
    out.append(f"| LTV | {debt/(tot['sreo_k']*1000)*100:.1f}% |")
    out.append(f"| Equity % | {tot['equity']/(tot['sreo_k']*1000)*100:.1f}% |")
    out.append("")

    # ---- Combined consolidated P&L ----
    out.append("## All-Funds Consolidated P&L (Jun 2025 – May 2026)")
    out.append("")
    out.append("| Account | " + " | ".join(MONTHS) + " |")
    out.append("|:--------|" + "|".join(["-------:"] * 13) + "|")

    def row(label, bold=False):
        vals = combined.get(label)
        if vals is None:
            return None
        cells = " | ".join(fmt(v) for v in vals)
        lab = f"**{label}**" if bold else label
        return f"| {lab} | {cells} |"

    for header, lines in PNL_ORDER:
        if lines is None:
            r = row(header, bold=True)
            if r:
                out.append(r)
            out.append("| | " + " | ".join([""] * 13) + " |")
        else:
            out.append(f"| **{header}** |" + " |" * 13)
            for label in lines:
                r = row(label)
                if r:
                    out.append(r)
    out.append("")

    # ---- Observations ----
    def t12(label):
        return combined.get(label, [0]*13)[12]
    inc = t12("Total Income")
    rm = t12("R&M Repairs") + t12("R&M Turns") + t12("R&M Grounds")
    capex = sum(t12(l) for l in ("CapEx Turns", "CapEx Appliances", "CapEx Disc", "CapEx Non-disc"))
    out.append("### Portfolio Observations")
    out.append("")
    out.append(f"- **T-12 NOI ${fmt(tot['noi'])}** on ${fmt(inc)} income; portfolio DSCR {tot['noi']/ds:.2f}.")
    out.append(f"- **Debt service (T-12) ${fmt(ds)}** ({ds/tot['noi']*100:.0f}% of NOI).")
    out.append(f"- **R&M (Repairs + Turns + Grounds) ${fmt(rm)}** = {rm/inc*100:.1f}% of income.")
    out.append(f"- **T-12 CapEx ${fmt(capex)}** = {capex/tot['noi']*100:.0f}% of NOI.")
    out.append(f"- **T-12 Cashflow ${fmt(tot['cf'])}** after debt service and CapEx.")
    inc_jun, inc_may = combined.get("Total Income", [0]*13)[0], combined.get("Total Income", [0]*13)[11]
    out.append(f"- **Income trend:** ${fmt(inc_jun)} (Jun) → ${fmt(inc_may)} (May), "
               f"{(inc_may/inc_jun-1)*100:+.1f}%.")
    out.append("")
    out.append("Generated from AppFolio on 2026.06.09. Rolls up fund-0, fund-i, fund-ii, "
               "fund-iii, fund-iv May summaries.")
    out.append("")

    target = BASE / "2026.06.09-all-funds-may-rollup.md"
    target.write_text("\n".join(out))
    print(f"Wrote: {target}")
    print(f"Totals: {int(tot['props'])} props, {int(tot['units'])} units, "
          f"NOI {fmt(tot['noi'])}, CF {fmt(tot['cf'])}, DSCR {tot['noi']/ds:.2f}, "
          f"flags 🟢{counts['🟢']} 🟡{counts['🟡']} 🔴{counts['🔴']}")


if __name__ == "__main__":
    main()
