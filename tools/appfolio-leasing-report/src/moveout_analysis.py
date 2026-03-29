"""
Calculate monthly moveout rates for 2025 and 2026.

Approach:
- The rent roll has `move_out` (current tenant's moveout, if any)
  and `last_move_out` (previous tenant's moveout from that unit).
- Collect all moveout dates from both fields across all units.
- Group by year and month, count moveouts per month.
- Normalize by portfolio size at the time (from property_directory management_start_date).
- 2025 = full year actuals
- 2026 = Jan/Feb actuals + Mar/Apr projected (from scheduled move_out dates)
- Generate comparison chart.
"""
import json
import os
import sys
from collections import Counter
from datetime import datetime, date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests

CLIENT_ID = os.environ.get("APPFOLIO_CLIENT_ID")
CLIENT_SECRET = os.environ.get("APPFOLIO_CLIENT_SECRET")
BASE = os.environ.get("APPFOLIO_BASE_URL", "https://mckay.appfolio.com")

if not CLIENT_ID or not CLIENT_SECRET:
    print("Error: APPFOLIO_CLIENT_ID and APPFOLIO_CLIENT_SECRET must be set")
    sys.exit(1)

AUTH = (CLIENT_ID, CLIENT_SECRET)

# --- Fetch property directory for portfolio size timeline ---
print("Fetching property directory...")
resp = requests.post(
    f"{BASE}/api/v2/reports/property_directory.json",
    auth=AUTH,
    headers={"Content-Type": "application/json"},
    json={},
    timeout=120,
)
props = resp.json().get("results", [])

# Build timeline of unit additions/removals
prop_events = []
for p in props:
    start = p.get("management_start_date")
    unit_count = p.get("units", 0)
    name = p.get("property_name", "?")
    end = p.get("management_end_date")

    if start and unit_count:
        prop_events.append((start, unit_count, name, "added"))
    if end and unit_count:
        prop_events.append((end, -unit_count, name, "removed"))

prop_events.sort(key=lambda x: x[0])


def units_at_month(year, month):
    """Total active units at the START of the given month."""
    cutoff = date(year, month, 1)
    total = 0
    for dt_str, units_delta, _, _ in prop_events:
        dt = datetime.strptime(dt_str, "%Y-%m-%d").date()
        if dt < cutoff:
            total += units_delta
    return total


# --- Fetch rent roll ---
print("Fetching rent roll data...")
resp = requests.post(
    f"{BASE}/api/v2/reports/rent_roll.json",
    auth=AUTH,
    headers={"Content-Type": "application/json"},
    json={},
    timeout=120,
)
data = resp.json()
units = data.get("results", [])

next_url = data.get("next_page_url")
while next_url:
    r = requests.get(next_url, auth=AUTH, timeout=120)
    if r.status_code == 200:
        d = r.json()
        units.extend(d.get("results", []))
        next_url = d.get("next_page_url")
    else:
        break

print(f"Total units (current): {len(units)}")

# --- Collect moveout dates ---
moveout_dates = []
sources = {"move_out": 0, "last_move_out": 0}

for u in units:
    for field in ["move_out", "last_move_out"]:
        val = u.get(field)
        if val:
            try:
                dt = datetime.strptime(str(val)[:10], "%Y-%m-%d")
                moveout_dates.append((dt, field, u.get("property_name", "?"), u.get("unit", "?")))
                sources[field] += 1
            except ValueError:
                pass

print(f"\nMoveout dates found:")
print(f"  From 'move_out':      {sources['move_out']}")
print(f"  From 'last_move_out': {sources['last_move_out']}")
print(f"  Total:                {len(moveout_dates)}")

# Deduplicate by (date, property, unit)
seen = set()
unique_moveouts = []
for dt, src, prop, unit in moveout_dates:
    key = (dt.strftime("%Y-%m-%d"), prop, unit)
    if key not in seen:
        seen.add(key)
        unique_moveouts.append((dt, src, prop, unit))

# --- Monthly counts ---
monthly_2025 = Counter()
for dt, _, _, _ in unique_moveouts:
    if dt.year == 2025:
        monthly_2025[dt.month] += 1

monthly_2026 = Counter()
for dt, _, _, _ in unique_moveouts:
    if dt.year == 2026:
        monthly_2026[dt.month] += 1

month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# --- Portfolio size table ---
print(f"\n{'=' * 60}")
print(f"  PORTFOLIO SIZE (units at start of month)")
print(f"{'=' * 60}")
print(f"\n{'Month':<8} {'2025':>8} {'2026':>8}")
print("-" * 26)
for m in range(1, 13):
    u25 = units_at_month(2025, m)
    u26 = units_at_month(2026, m)
    print(f"  {month_names[m-1]:<6} {u25:>8} {u26:>8}")

# --- Normalized rates ---
print(f"\n{'=' * 60}")
print(f"  2025 MOVEOUTS (NORMALIZED)")
print(f"{'=' * 60}")
print(f"\n{'Month':<8} {'Units':>8} {'Moveouts':>10} {'Rate':>10}")
print("-" * 38)
rates_2025 = []
for m in range(1, 13):
    u = units_at_month(2025, m)
    count = monthly_2025.get(m, 0)
    rate = (count / u) * 100 if u > 0 else 0
    rates_2025.append(rate)
    print(f"  {month_names[m-1]:<6} {u:>8} {count:>10} {rate:>9.2f}%")

total_2025 = sum(monthly_2025.values())
avg_rate_2025 = sum(rates_2025) / 12
print(f"\n  Total moveouts: {total_2025}  |  Avg monthly rate: {avg_rate_2025:.2f}%  |  Annualized: {avg_rate_2025*12:.1f}%")

print(f"\n{'=' * 60}")
print(f"  2026 MOVEOUTS (NORMALIZED)")
print(f"{'=' * 60}")
print(f"\n{'Month':<8} {'Units':>8} {'Moveouts':>10} {'Rate':>10}  {'Type':<12}")
print("-" * 55)
rates_2026 = []
for m in range(1, 13):
    u = units_at_month(2026, m)
    # Override with actual current portfolio size starting March 2026
    if m >= 3:
        u = 1035
    count = monthly_2026.get(m, 0)
    rate = (count / u) * 100 if u > 0 else 0
    rates_2026.append(rate)
    if count == 0 and m > 4:
        continue
    label = "actual" if m <= 2 else "projected"
    print(f"  {month_names[m-1]:<6} {u:>8} {count:>10} {rate:>9.2f}%  {label:<12}")

later = {m: c for m, c in monthly_2026.items() if m > 4}
if later:
    print(f"\n  Later scheduled moveouts:")
    for m in sorted(later):
        u = 1035  # Use current portfolio size for 2026
        rate = (later[m] / u) * 100 if u > 0 else 0
        print(f"    {month_names[m-1]:<6} {u:>8} {later[m]:>10} {rate:>9.2f}%")

# --- Detail for March/April ---
moveouts_2026_list = [(dt, src, prop, unit) for dt, src, prop, unit in unique_moveouts if dt.year == 2026]

print(f"\n{'=' * 60}")
print(f"  MARCH 2026 PROJECTED MOVEOUTS (DETAIL)")
print(f"{'=' * 60}")
mar = sorted([(dt, prop, unit) for dt, _, prop, unit in moveouts_2026_list if dt.month == 3], key=lambda x: (x[1], x[2]))
for dt, prop, unit in mar:
    print(f"  {dt.strftime('%Y-%m-%d')}  {prop:<40} Unit {unit}")

print(f"\n{'=' * 60}")
print(f"  APRIL 2026 PROJECTED MOVEOUTS (DETAIL)")
print(f"{'=' * 60}")
apr = sorted([(dt, prop, unit) for dt, _, prop, unit in moveouts_2026_list if dt.month == 4], key=lambda x: (x[1], x[2]))
for dt, prop, unit in apr:
    print(f"  {dt.strftime('%Y-%m-%d')}  {prop:<40} Unit {unit}")

# --- Summary ---
print(f"\n{'=' * 60}")
print(f"  SUMMARY")
print(f"{'=' * 60}")
print(f"  Portfolio (Jan 2025):         {units_at_month(2025, 1)}")
print(f"  Portfolio (current):          {units_at_month(2026, 3)}")
print(f"  2025 total moveouts:          {total_2025}")
print(f"  2025 avg monthly rate:        {avg_rate_2025:.2f}%")
print(f"  2026 Jan (actual):            {monthly_2026.get(1, 0)}  ({rates_2026[0]:.2f}%)")
print(f"  2026 Feb (actual):            {monthly_2026.get(2, 0)}  ({rates_2026[1]:.2f}%)")
print(f"  2026 Mar (projected):         {monthly_2026.get(3, 0)}  ({rates_2026[2]:.2f}%)")
print(f"  2026 Apr (projected):         {monthly_2026.get(4, 0)}  ({rates_2026[3]:.2f}%)")

print(f"\n\u26a0\ufe0f  CAVEAT:")
print(f"  The rent roll is a point-in-time snapshot.")
print(f"  'last_move_out' = previous tenant's moveout for each unit.")
print(f"  'move_out' = current/recent tenant's scheduled moveout date.")
print(f"  A unit that turned over TWICE may only show the most recent moveout.")
print(f"  April projections are likely incomplete (notices not yet filed).")

# --- Chart: line graph, normalized to % of portfolio at the time ---
import os
months = list(range(1, 13))

# 2026: only show through last month with data
last_2026_month = max((m for m in months if monthly_2026.get(m, 0) > 0), default=0)

fig, ax = plt.subplots(figsize=(14, 6))

x = list(range(len(months)))

# 2025 full line
ax.plot(x, rates_2025, color="#3498db", linewidth=2.5, marker="o", markersize=6,
        label="2025 (Actual)", zorder=3)

# 2026 actual (Jan-Feb) — solid line
ax.plot(x[:2], rates_2026[:2], color="#e74c3c", linewidth=2.5, marker="o",
        markersize=6, label="2026 (Actual)", zorder=3)

# 2026 projected (Feb-Apr+) — dashed line, connected from Feb
proj_x = list(range(1, last_2026_month))
proj_y = [rates_2026[1]] + rates_2026[2:last_2026_month]
ax.plot(proj_x, proj_y, color="#e74c3c", linewidth=2.5, marker="o",
        markersize=6, linestyle="--", alpha=0.6, label="2026 (Projected)", zorder=3)

# Data labels
for i, v in enumerate(rates_2025):
    if v > 0:
        ax.annotate(f"{v:.1f}%", (i, v), textcoords="offset points",
                    xytext=(0, 10), ha="center", fontsize=8, color="#2c3e50")

for i in range(last_2026_month):
    v = rates_2026[i]
    if v > 0:
        ax.annotate(f"{v:.1f}%", (i, v), textcoords="offset points",
                    xytext=(0, -15), ha="center", fontsize=8, color="#c0392b")

ax.set_xlabel("Month", fontsize=12)
ax.set_ylabel("Moveout Rate (% of portfolio)", fontsize=12)
ax.set_title("m5x2 Monthly Moveout Rate — 2025 vs 2026\n(normalized by portfolio size at time)", fontsize=14, fontweight="bold")
ax.set_xticks(x)
ax.set_xticklabels(month_names, fontsize=10)
ax.legend(loc="upper left", fontsize=10)
ax.set_ylim(0, max(max(rates_2025), max(rates_2026[:last_2026_month])) + 1.5)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.grid(axis="y", alpha=0.3)

# Add "as of" date annotation
today_str = date.today().strftime("%B %d, %Y")
ax.text(0.98, 0.02, f"Data as of {today_str}", transform=ax.transAxes,
        fontsize=9, ha="right", va="bottom", color="#7f8c8d", style="italic")

plt.tight_layout()

output_path = os.path.expanduser("~/vault/h335/stats/moveouts_2025_vs_2026.png")
fig.savefig(output_path, dpi=150, bbox_inches="tight")
print(f"\nChart saved to {output_path}")
plt.close()
