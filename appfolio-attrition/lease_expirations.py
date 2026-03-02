"""
Attrition summary from AppFolio lease_history, rent_roll, and property_directory APIs.

Columns:
  - Month
  - Leases Expiring (lease_history: LeaseEnd in that month, signed leases)
  - Total Units (cumulative from property_directory management_start_date)
  - Released (new units added that month from property acquisitions)
  - Renewals Signed (lease_history: Renewal=Yes, Status=Completed, by LeaseStart month)
  - Transitioned to M2M (lease_history: Status='Month To Month', by LeaseStart month)
"""
import csv
import os
from collections import Counter, defaultdict
from datetime import datetime

import requests

CLIENT_ID = os.environ["APPFOLIO_CLIENT_ID"]
CLIENT_SECRET = os.environ["APPFOLIO_CLIENT_SECRET"]
BASE = os.environ.get("APPFOLIO_BASE_URL", "https://mckay.appfolio.com")

OUTPUT_FILE = "attrition_summary.csv"


def fetch_json_get(url, params=None):
    """GET with auth and pagination."""
    resp = requests.get(url, auth=(CLIENT_ID, CLIENT_SECRET), params=params, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    results = data["results"]

    next_page = data.get("next_page_url")
    while next_page:
        r = requests.get(next_page, auth=(CLIENT_ID, CLIENT_SECRET), timeout=120)
        r.raise_for_status()
        page = r.json()
        results.extend(page["results"])
        next_page = page.get("next_page_url")

    return results


def fetch_json_post(url, body=None):
    """POST with auth and pagination (v2 API)."""
    resp = requests.post(
        url, auth=(CLIENT_ID, CLIENT_SECRET),
        headers={"Content-Type": "application/json"},
        json=body or {}, timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data["results"]

    next_page = data.get("next_page_url")
    while next_page:
        r = requests.get(next_page, auth=(CLIENT_ID, CLIENT_SECRET), timeout=120)
        r.raise_for_status()
        page = r.json()
        results.extend(page["results"])
        next_page = page.get("next_page_url")

    return results


def parse_date(val, fmt="%m/%d/%Y"):
    if not val:
        return None
    try:
        return datetime.strptime(val.strip(), fmt)
    except ValueError:
        return None


def main():
    # --- Month range ---
    months = []
    for y in (2022, 2023, 2024, 2025, 2026):
        for m in range(1, (12 if y in (2022, 2023, 2024, 2025) else 6) + 1):
            months.append((y, m))
    month_set = set(months)

    # --- Pull lease history ---
    print("Pulling lease history...")
    leases = fetch_json_get(
        f"{BASE}/api/v1/reports/lease_history.json",
        params={"from_date": "2021-01-01", "to_date": "2026-06-30"},
    )
    print(f"  {len(leases)} lease records")

    # --- Pull rent roll for moveout data and unit count ---
    print("Pulling rent roll...")
    rent_roll = fetch_json_get(f"{BASE}/api/v1/reports/rent_roll.json")
    print(f"  {len(rent_roll)} units")

    # --- Pull property directory for unit growth ---
    print("Pulling property directory...")
    props = fetch_json_post(f"{BASE}/api/v2/reports/property_directory.json")
    print(f"  {len(props)} properties")

    # --- Build unit growth timeline from management_start_date ---
    units_added = defaultdict(int)
    for p in props:
        date_str = p.get("management_start_date") or p.get("property_created_on")
        unit_count = p.get("units", 0) or 0
        if date_str and unit_count:
            dt = parse_date(date_str, "%Y-%m-%d")
            if dt:
                units_added[(dt.year, dt.month)] += unit_count

    # Cumulative units: start with everything before Jan 2022
    pre_start = sum(u for (y, m), u in units_added.items() if (y, m) < (2022, 1))

    cumulative_units = {}
    released = {}
    running = pre_start
    for y, m in months:
        added = units_added.get((y, m), 0)
        running += added
        cumulative_units[(y, m)] = running
        released[(y, m)] = added

    # --- Count: Leases Expiring (by LeaseEnd month) ---
    expiring = Counter()
    for rec in leases:
        le = parse_date(rec.get("LeaseEnd"))
        status = rec.get("Status", "")
        if le and (le.year, le.month) in month_set:
            if status in ("Completed", "Status Cannot Be Determined"):
                expiring[(le.year, le.month)] += 1

    # --- Count: Renewals Signed (by LeaseStart month) ---
    renewals = Counter()
    for rec in leases:
        ls = parse_date(rec.get("LeaseStart"))
        if (
            ls
            and (ls.year, ls.month) in month_set
            and rec.get("Renewal") == "Yes"
            and rec.get("Status") == "Completed"
        ):
            renewals[(ls.year, ls.month)] += 1

    # --- Count: Transitioned to M2M (by LeaseStart month) ---
    m2m = Counter()
    for rec in leases:
        ls = parse_date(rec.get("LeaseStart"))
        if (
            ls
            and (ls.year, ls.month) in month_set
            and rec.get("Status") == "Month To Month"
        ):
            m2m[(ls.year, ls.month)] += 1

    # --- Count: Moveouts (by MoveOut date month) ---
    # Combine lease_history and rent_roll sources, deduplicate by UnitId+date.
    # Split into regular moveouts (fixed-term) and M2M moveouts.
    moveouts = Counter()
    m2m_moveouts = Counter()
    seen_moveouts = set()

    # A moveout is M2M if the tenant's most recent lease had already expired
    # (or never had a lease end date) at the time of moveout.

    # Source 1: lease_history — group by (UnitId, MoveOut date), pick the
    # record with the latest LeaseEnd to avoid using stale old lease data.
    moveout_records = {}  # (UnitId, moveout_date_str) -> best record
    for rec in leases:
        mo = parse_date(rec.get("MoveOut"))
        if mo and (mo.year, mo.month) in month_set:
            dedup_key = (rec.get("UnitId"), mo.strftime("%Y-%m-%d"))
            le = parse_date(rec.get("LeaseEnd"))
            existing = moveout_records.get(dedup_key)
            if existing is None:
                moveout_records[dedup_key] = rec
            else:
                # Keep the record with the latest LeaseEnd
                existing_le = parse_date(existing.get("LeaseEnd"))
                if le and (existing_le is None or le > existing_le):
                    moveout_records[dedup_key] = rec

    for dedup_key, rec in moveout_records.items():
        seen_moveouts.add(dedup_key)
        mo = parse_date(rec.get("MoveOut"))
        le = parse_date(rec.get("LeaseEnd"))
        is_m2m = (
            rec.get("Status") == "Month To Month"
            or le is None
            or mo > le
        )
        if is_m2m:
            m2m_moveouts[(mo.year, mo.month)] += 1
        else:
            moveouts[(mo.year, mo.month)] += 1

    # Note: rent_roll MoveOut/LastMoveOut excluded — lease_history is more
    # reliable and avoids double-counting from stale rent roll snapshots.

    # --- Build output ---
    fieldnames = [
        "Month",
        "Total Units",
        "Added",
        "Leases Expiring",
        "Renewals Signed",
        "Transitioned to M2M",
        "Moveouts",
        "M2M Moveouts",
        "Monthly Moveout Rate",
    ]
    rows = []
    for y, mo in months:
        total = cumulative_units[(y, mo)]
        total_moveouts = moveouts.get((y, mo), 0) + m2m_moveouts.get((y, mo), 0)
        rate = (total_moveouts / total * 100) if total else 0
        rows.append({
            "Month": datetime(y, mo, 1).strftime("%b %Y"),
            "Total Units": total,
            "Added": released[(y, mo)],
            "Leases Expiring": expiring.get((y, mo), 0),
            "Renewals Signed": renewals.get((y, mo), 0),
            "Transitioned to M2M": m2m.get((y, mo), 0),
            "Moveouts": moveouts.get((y, mo), 0),
            "M2M Moveouts": m2m_moveouts.get((y, mo), 0),
            "Monthly Moveout Rate": f"{rate:.1f}%",
        })

    # --- Print ---
    hdr = (
        f"  {'Month':<10} {'Units':>6} {'Addd':>5} {'Expir':>6}"
        f" {'Renew':>6} {'M2M':>5} {'MvOut':>6} {'M2M MO':>7} {'Rate':>6}"
    )
    print(f"\n{hdr}")
    print("  " + "-" * (len(hdr) - 2))
    for row in rows:
        print(
            f"  {row['Month']:<10}"
            f" {row['Total Units']:>6}"
            f" {row['Added']:>5}"
            f" {row['Leases Expiring']:>6}"
            f" {row['Renewals Signed']:>6}"
            f" {row['Transitioned to M2M']:>5}"
            f" {row['Moveouts']:>6}"
            f" {row['M2M Moveouts']:>7}"
            f" {row['Monthly Moveout Rate']:>6}"
        )

    # --- Write CSV ---
    with open(OUTPUT_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nSaved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
