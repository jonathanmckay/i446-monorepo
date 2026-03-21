#!/usr/bin/env python3
"""
Daily Donut Chart Generator

Fetches Toggl data for a specified date and generates a donut chart for Neon.

Usage:
    python3 daily-donut.py 2026-03-14
    python3 daily-donut.py --yesterday
    python3 daily-donut.py --today
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path for importing toggl_server
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))

# Load environment variables from .env file
env_file = parent_dir / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value
                # Also set TOGGL_API_KEY if TOGGL_API_TOKEN is found
                if key == "TOGGL_API_TOKEN":
                    os.environ["TOGGL_API_KEY"] = value

from toggl_server.toggl_api import get_entries
from toggl_server.config import PROJECT_NAMES


def get_project_breakdown(entries):
    """
    Aggregate entries by project and return minutes per project.

    Returns:
        dict: {project_code: minutes}
    """
    project_totals = {}

    for e in entries:
        dur = e.get("duration", 0)
        if dur > 0:
            proj_id = e.get("project_id")
            proj_code = PROJECT_NAMES.get(proj_id, "no project") if proj_id else "no project"
            project_totals[proj_code] = project_totals.get(proj_code, 0) + (dur // 60)

    return project_totals


def generate_chart(date_str, project_data):
    """
    Call the donut chart generator script.

    Args:
        date_str: Date string (YYYY-MM-DD)
        project_data: Dict of {project: minutes}

    Returns:
        bool: True if successful
    """
    # Path to donut generator script
    script_path = Path.home() / "vault" / "i447" / "i446" / "toggl-donut-generator.py"

    if not script_path.exists():
        print(f"✗ Chart generator not found at {script_path}")
        return False

    # Convert to JSON
    data_json = json.dumps(project_data)

    # Run the generator
    cmd = [
        "python3",
        str(script_path),
        "--date", date_str,
        "--data", data_json
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"✗ Chart generation failed:")
        print(result.stderr)
        return False

    print(result.stdout)
    return True


def add_sleep_minutes(date_str, project_data):
    """
    Add sleep minutes to column D (睡觉) in the 0₦ sheet.

    Args:
        date_str: Date string (YYYY-MM-DD)
        project_data: Dict of {project: minutes}

    Returns:
        bool: True if successful
    """
    sleep_mins = project_data.get('睡觉', 0)
    if sleep_mins == 0:
        print("No sleep data found, skipping column D update")
        return True

    # Calculate target row (row 6 = 2026-01-04, per 0₦ sheet structure)
    target_date = datetime.fromisoformat(date_str)
    start_date = datetime(2026, 1, 4)
    days_diff = (target_date - start_date).days
    target_row = 6 + days_diff

    # AppleScript to write to column D in 0₦ sheet
    applescript = f'''
tell application "Microsoft Excel"
    set theSheet to sheet "0₦" of workbook 1
    set theCell to cell "D{target_row}" of theSheet
    set value of theCell to {sleep_mins}
end tell
'''

    try:
        result = subprocess.run(
            ['osascript', '-e', applescript],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            print(f"✓ Added {sleep_mins} sleep minutes to D{target_row}")
            return True
        else:
            print(f"✗ Failed to add sleep minutes: {result.stderr}")
            return False

    except Exception as e:
        print(f"✗ Failed to add sleep minutes: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Generate daily donut chart from Toggl data')
    parser.add_argument('date', nargs='?', help='Date (YYYY-MM-DD)')
    parser.add_argument('--yesterday', action='store_true', help='Use yesterday\'s date')
    parser.add_argument('--today', action='store_true', help='Use today\'s date')

    args = parser.parse_args()

    # Determine target date
    if args.yesterday:
        target_date = (datetime.now() - timedelta(days=1)).date()
    elif args.today:
        target_date = datetime.now().date()
    elif args.date:
        try:
            target_date = datetime.fromisoformat(args.date).date()
        except ValueError:
            print(f"✗ Invalid date format: {args.date}")
            print("  Use YYYY-MM-DD format")
            return 1
    else:
        print("✗ Please specify a date, --yesterday, or --today")
        parser.print_help()
        return 1

    date_str = target_date.isoformat()

    print(f"Fetching Toggl data for {date_str}...")

    # Fetch entries from Toggl
    try:
        entries = get_entries(
            start_date=date_str,
            end_date=(target_date + timedelta(days=1)).isoformat()
        )
    except Exception as e:
        print(f"✗ Failed to fetch Toggl data: {e}")
        return 1

    if not entries:
        print(f"✗ No entries found for {date_str}")
        return 1

    # Aggregate by project
    project_data = get_project_breakdown(entries)

    print(f"Found {len(entries)} entries across {len(project_data)} projects")
    for proj, mins in sorted(project_data.items(), key=lambda x: x[1], reverse=True):
        print(f"  {proj}: {mins}m")

    # Generate the chart
    print(f"\nGenerating donut chart...")
    chart_success = generate_chart(date_str, project_data)

    # Add sleep minutes to column D
    print(f"\nAdding sleep minutes to 0₦ sheet...")
    sleep_success = add_sleep_minutes(date_str, project_data)

    if chart_success and sleep_success:
        return 0
    else:
        return 1


if __name__ == '__main__':
    exit(main())
