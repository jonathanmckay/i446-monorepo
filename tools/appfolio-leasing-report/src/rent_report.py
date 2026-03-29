"""
Pull rent roll from AppFolio Reports API and calculate average rent.

Usage:
  python3 src/rent_report.py                    # uses env vars
  python3 src/rent_report.py --output rent.csv  # also save CSV
"""
import os
import sys
import csv
import io
import argparse
from datetime import datetime

import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()


def get_credentials():
    client_id = os.environ.get("APPFOLIO_CLIENT_ID")
    client_secret = os.environ.get("APPFOLIO_CLIENT_SECRET")
    api_url = os.environ.get("APPFOLIO_API_URL", "").rstrip("/")

    if not all([client_id, client_secret, api_url]):
        print("Missing env vars. Need: APPFOLIO_CLIENT_ID, APPFOLIO_CLIENT_SECRET, APPFOLIO_API_URL")
        sys.exit(1)

    return client_id, client_secret, api_url


def pull_rent_roll(client_id: str, client_secret: str, api_url: str) -> pd.DataFrame:
    """
    Pull rent roll report from AppFolio Reports API.

    Tries multiple endpoint patterns since the exact path varies by API version.
    Uses HTTP Basic Auth with client_id:client_secret.
    """
    # AppFolio Reports API uses v1 with Basic Auth
    # Override to v1 if user set v2
    base = api_url.replace("/api/v2", "/api/v1").replace("/api/v1/", "/api/v1")

    endpoints = [
        "/reports/rent_roll.json",
        "/reports/rent_roll.csv",
        "/reports/rent_roll",
    ]

    headers = {"Accept": "application/json, text/csv"}

    for endpoint in endpoints:
        url = f"{base}{endpoint}"
        print(f"Trying: {url}")

        resp = requests.get(
            url,
            auth=(client_id, client_secret),
            headers=headers,
            params={"as_of_date": datetime.now().strftime("%Y-%m-%d")},
            timeout=120,
        )

        if resp.status_code == 200:
            content_type = resp.headers.get("Content-Type", "")

            if "csv" in content_type or resp.text.strip().startswith('"') or "," in resp.text.split("\n")[0]:
                df = pd.read_csv(io.StringIO(resp.text))
                print(f"Success: {len(df)} rows from {endpoint}")
                return df
            elif "json" in content_type or endpoint.endswith(".json"):
                data = resp.json()
                results = []
                if isinstance(data, list):
                    results = data
                elif isinstance(data, dict) and "results" in data:
                    results = data["results"]
                    # Handle pagination
                    next_page = data.get("next_page_url")
                    while next_page:
                        print(f"  Fetching next page...")
                        page_resp = requests.get(
                            next_page,
                            auth=(client_id, client_secret),
                            timeout=120,
                        )
                        if page_resp.status_code == 200:
                            page_data = page_resp.json()
                            results.extend(page_data.get("results", []))
                            next_page = page_data.get("next_page_url")
                        else:
                            break

                if results:
                    df = pd.DataFrame(results)
                    print(f"Success: {len(df)} rows (JSON) from {endpoint}")
                    return df

            print(f"  Got 200 but unexpected format: {content_type}")
        else:
            print(f"  {resp.status_code}: {resp.text[:200]}")

    print("\nCould not find a working rent roll endpoint.")
    print("Check your API URL and credentials. You may need to verify the")
    print("exact endpoint path in your AppFolio API documentation.")
    sys.exit(1)


def find_rent_column(df: pd.DataFrame) -> str:
    """Find the column containing rent amounts."""
    candidates = [
        "rent", "rent_amount", "monthly_rent", "market_rent",
        "current_rent", "Rent", "Rent Amount", "Monthly Rent",
        "Market Rent", "Current Rent", "Charge Amount",
    ]
    for col in candidates:
        if col in df.columns:
            return col

    # Fuzzy match
    for col in df.columns:
        if "rent" in col.lower():
            return col

    print(f"Could not find rent column. Available columns: {list(df.columns)}")
    sys.exit(1)


def find_unit_column(df: pd.DataFrame) -> str:
    """Find the column containing unit identifiers."""
    candidates = [
        "unit", "unit_number", "Unit", "Unit Number", "Unit #",
        "unit_name", "Unit Name",
    ]
    for col in candidates:
        if col in df.columns:
            return col
    for col in df.columns:
        if "unit" in col.lower():
            return col
    return None


def find_status_column(df: pd.DataFrame) -> str:
    """Find occupancy/status column to filter current tenants."""
    candidates = [
        "status", "occupancy", "lease_status", "Status",
        "Occupancy", "Lease Status", "Occupancy Status",
    ]
    for col in candidates:
        if col in df.columns:
            return col
    for col in df.columns:
        if "status" in col.lower() or "occup" in col.lower():
            return col
    return None


def clean_currency(val) -> float:
    """Parse currency string to float."""
    if pd.isna(val):
        return 0.0
    s = str(val).replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def analyze_rent(df: pd.DataFrame, output_path: str = None):
    """Calculate and print rent statistics."""
    rent_col = find_rent_column(df)
    unit_col = find_unit_column(df)
    status_col = find_status_column(df)

    # Clean rent values
    df["_rent"] = df[rent_col].apply(clean_currency)

    # Filter to occupied units if status column exists
    if status_col:
        occupied_terms = ["occupied", "current", "active", "leased"]
        mask = df[status_col].astype(str).str.lower().isin(occupied_terms)
        if mask.any():
            df_current = df[mask].copy()
            print(f"\nFiltered to {len(df_current)} occupied units (from {len(df)} total)")
        else:
            print(f"\nStatus values found: {df[status_col].unique()}")
            print("Could not filter by occupancy — showing all units")
            df_current = df.copy()
    else:
        df_current = df.copy()

    # Remove zero-rent rows
    df_current = df_current[df_current["_rent"] > 0]

    # Stats
    total_units = len(df_current)
    total_rent = df_current["_rent"].sum()
    avg_rent = df_current["_rent"].mean()
    median_rent = df_current["_rent"].median()
    min_rent = df_current["_rent"].min()
    max_rent = df_current["_rent"].max()

    print("\n" + "=" * 50)
    print("  RENT ROLL SUMMARY")
    print("=" * 50)
    print(f"  Date:          {datetime.now().strftime('%Y-%m-%d')}")
    print(f"  Total Units:   {total_units}")
    print(f"  Total Rent:    ${total_rent:,.2f}/mo")
    print(f"  Average Rent:  ${avg_rent:,.2f}/mo")
    print(f"  Median Rent:   ${median_rent:,.2f}/mo")
    print(f"  Min Rent:      ${min_rent:,.2f}/mo")
    print(f"  Max Rent:      ${max_rent:,.2f}/mo")
    print("=" * 50)

    # Per-unit breakdown
    if unit_col and total_units <= 50:
        print(f"\n{'Unit':<15} {'Rent':>12}")
        print("-" * 27)
        for _, row in df_current.sort_values("_rent", ascending=False).iterrows():
            unit = row[unit_col] if unit_col else "—"
            print(f"  {str(unit):<13} ${row['_rent']:>10,.2f}")

    # Save CSV if requested
    if output_path:
        df_current.to_csv(output_path, index=False)
        print(f"\nSaved to {output_path}")

    # Write summary for GitHub Actions
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a") as f:
            f.write(f"## Rent Roll Summary — {datetime.now().strftime('%Y-%m-%d')}\n\n")
            f.write(f"| Metric | Value |\n|--------|-------|\n")
            f.write(f"| Total Units | {total_units} |\n")
            f.write(f"| Total Rent | ${total_rent:,.2f}/mo |\n")
            f.write(f"| **Average Rent** | **${avg_rent:,.2f}/mo** |\n")
            f.write(f"| Median Rent | ${median_rent:,.2f}/mo |\n")
            f.write(f"| Min | ${min_rent:,.2f} |\n")
            f.write(f"| Max | ${max_rent:,.2f} |\n")

    return {
        "total_units": total_units,
        "total_rent": total_rent,
        "average_rent": avg_rent,
        "median_rent": median_rent,
    }


def main():
    parser = argparse.ArgumentParser(description="Pull AppFolio rent roll and calculate average rent")
    parser.add_argument("--output", "-o", help="Save rent roll CSV to this path")
    args = parser.parse_args()

    client_id, client_secret, api_url = get_credentials()
    df = pull_rent_roll(client_id, client_secret, api_url)

    print(f"\nColumns: {list(df.columns)}")
    analyze_rent(df, output_path=args.output)


if __name__ == "__main__":
    main()
