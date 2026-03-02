"""
Calculate average monthly moveout rate for 2025.

Approach:
- The rent roll has `move_out` (current tenant's moveout, if any) 
  and `last_move_out` (previous tenant's moveout from that unit).
- Collect all moveout dates from both fields across all 915 units.
- Group by month in 2025, count moveouts per month.
- Divide by total units to get monthly turnover rate.

We also check if AppFolio has a dedicated move-out report.
"""
import json
from collections import Counter
from datetime import datetime

import requests

AUTH = ("YOUR_CLIENT_ID", "YOUR_CLIENT_SECRET")
BASE = "https://mckay.appfolio.com"

# --- Step 1: Try dedicated move-out / vacancy report ---
print("=" * 60)
print("  STEP 1: Check for dedicated move-out reports")
print("=" * 60)

alt_reports = [
    "move_out",
    "move_outs",
    "vacancy",
    "turnover",
    "unit_vacancy",
    "tenant_move_out",
]
for rpt in alt_reports:
    url = f"{BASE}/api/v2/reports/{rpt}.json"
    resp = requests.post(url, auth=AUTH, headers={"Content-Type": "application/json"}, json={}, timeout=30)
    print(f"  {rpt}: {resp.status_code}")

# --- Step 2: Analyze rent roll data ---
print(f"\n{'=' * 60}")
print("  STEP 2: Analyze moveouts from rent roll data")
print("=" * 60)

resp = requests.post(
    f"{BASE}/api/v2/reports/rent_roll.json",
    auth=AUTH,
    headers={"Content-Type": "application/json"},
    json={},
    timeout=120,
)
data = resp.json()
units = data.get("results", [])

# Pagination
next_url = data.get("next_page_url")
while next_url:
    r = requests.get(next_url, auth=AUTH, timeout=120)
    if r.status_code == 200:
        d = r.json()
        units.extend(d.get("results", []))
        next_url = d.get("next_page_url")
    else:
        break

print(f"Total units: {len(units)}")

# Collect all moveout dates
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

# --- Step 3: Filter to 2025 and group by month ---
print(f"\n{'=' * 60}")
print("  STEP 3: Monthly moveouts in 2025")
print("=" * 60)

moveouts_2025 = [(dt, src, prop, unit) for dt, src, prop, unit in moveout_dates if dt.year == 2025]
print(f"Total moveouts in 2025: {len(moveouts_2025)}")

# Deduplicate: a unit might have move_out AND last_move_out in 2025
# But they represent different events. last_move_out = previous tenant,
# move_out = current/recent tenant. So we keep both as separate events.
# However, if same unit has SAME date in both fields, dedupe.
seen = set()
unique_moveouts = []
for dt, src, prop, unit in moveouts_2025:
    key = (dt.strftime("%Y-%m-%d"), prop, unit)
    if key not in seen:
        seen.add(key)
        unique_moveouts.append((dt, src, prop, unit))

print(f"Unique moveouts in 2025 (deduped by unit+date): {len(unique_moveouts)}")

# Group by month
monthly = Counter()
for dt, _, _, _ in unique_moveouts:
    monthly[dt.month] += 1

total_units = len(units)
month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", 
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

print(f"\n{'Month':<8} {'Moveouts':>10} {'% of Units':>12}")
print("-" * 32)

rates = []
for m in range(1, 13):
    count = monthly.get(m, 0)
    pct = (count / total_units) * 100 if total_units > 0 else 0
    rates.append(pct)
    print(f"  {month_names[m]:<6} {count:>10} {pct:>11.2f}%")

# Average
months_with_data = [m for m in range(1, 13) if monthly.get(m, 0) > 0]
all_months = list(range(1, 13))  # full year

avg_moveouts = sum(monthly.values()) / 12
avg_rate = (avg_moveouts / total_units) * 100 if total_units > 0 else 0

print(f"\n{'=' * 60}")
print(f"  SUMMARY")
print(f"{'=' * 60}")
print(f"  Total units:               {total_units}")
print(f"  Total moveouts in 2025:    {len(unique_moveouts)}")
print(f"  Avg moveouts/month:        {avg_moveouts:.1f}")
print(f"  Avg monthly turnover rate: {avg_rate:.2f}%")
print(f"  Annualized turnover:       {avg_rate * 12:.1f}%")
print(f"{'=' * 60}")

# --- Step 4: Caveat ---
print(f"\n⚠️  CAVEAT:")
print(f"  The rent roll is a point-in-time snapshot.")
print(f"  'last_move_out' = previous tenant's moveout for each unit.")
print(f"  'move_out' = current/recent tenant's moveout date.")
print(f"  A unit that turned over TWICE in 2025 may only show the")
print(f"  most recent moveout. Actual turnover could be higher.")
print(f"  For exact numbers, check AppFolio's Vacancy report.")
