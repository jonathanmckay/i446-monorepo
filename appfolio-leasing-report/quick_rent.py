"""Quick rent analysis from live AppFolio API."""
import json
import os
import sys
import requests

CLIENT_ID = os.environ.get("APPFOLIO_CLIENT_ID")
CLIENT_SECRET = os.environ.get("APPFOLIO_CLIENT_SECRET")
BASE_URL = os.environ.get("APPFOLIO_BASE_URL", "https://mckay.appfolio.com")
API_URL = f"{BASE_URL}/api/v1/reports/rent_roll.json"

if not CLIENT_ID or not CLIENT_SECRET:
    print("Error: APPFOLIO_CLIENT_ID and APPFOLIO_CLIENT_SECRET must be set")
    sys.exit(1)

resp = requests.get(API_URL, auth=(CLIENT_ID, CLIENT_SECRET), timeout=60)
resp.raise_for_status()
data = resp.json()
units = data["results"]

# Check for pagination
next_page = data.get("next_page_url")
while next_page:
    resp = requests.get(next_page, auth=(CLIENT_ID, CLIENT_SECRET), timeout=60)
    resp.raise_for_status()
    page = resp.json()
    units.extend(page["results"])
    next_page = page.get("next_page_url")

print(f"Total units in rent roll: {len(units)}")

# Status breakdown
statuses = {}
for u in units:
    s = u.get("Status", "Unknown")
    statuses[s] = statuses.get(s, 0) + 1

print("\nStatus breakdown:")
for s, c in sorted(statuses.items(), key=lambda x: -x[1]):
    print(f"  {s}: {c}")

# Filter to occupied
occupied = [u for u in units if u.get("Status", "").lower() in ("occupied", "current")]
if not occupied:
    # Fallback: all units with rent > 0
    occupied = units

# Extract rents
rents = []
for u in occupied:
    try:
        r = float(str(u.get("Rent", 0)).replace(",", "").replace("$", ""))
        if r > 0:
            rents.append(r)
    except (ValueError, TypeError):
        pass

if not rents:
    print("No rent data found!")
    sys.exit(1)

rents.sort()
mid = len(rents) // 2
median = rents[mid] if len(rents) % 2 else (rents[mid - 1] + rents[mid]) / 2

print(f"\n{'=' * 45}")
print(f"  RENT SUMMARY")
print(f"{'=' * 45}")
print(f"  Units with rent:  {len(rents)}")
print(f"  Total rent:       ${sum(rents):,.2f}/mo")
print(f"  Average rent:     ${sum(rents) / len(rents):,.2f}/mo")
print(f"  Median rent:      ${median:,.2f}/mo")
print(f"  Min rent:         ${min(rents):,.2f}/mo")
print(f"  Max rent:         ${max(rents):,.2f}/mo")
print(f"{'=' * 45}")
