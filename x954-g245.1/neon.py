import argparse
import sys
import os
import datetime
from neon_server.excel_handler import NeonHandler

def main():
    parser = argparse.ArgumentParser(description="Neon Excel Agent CLI")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Status Command
    status_parser = subparsers.add_parser("status", help="Show today's status")
    status_parser.add_argument("--sheet", default="0s", help="Target sheet (default: 0s)")
    status_parser.add_argument("--date", help="Date in YYYY-MM-DD format (or 'yesterday')")

    # Log Command
    log_parser = subparsers.add_parser("log", help="Log a value to a column")
    log_parser.add_argument("column", help="Column name or index")
    log_parser.add_argument("value", help="Value to set")
    log_parser.add_argument("--sheet", default="0s", help="Target sheet (default: 0s)")

    # Inspect Command
    inspect_parser = subparsers.add_parser("inspect", help="Inspect sheet headers/structure")
    inspect_parser.add_argument("--sheet", default="0分", help="Target sheet")

    # Points Command
    points_parser = subparsers.add_parser("points", help="Get points breakdown")
    points_parser.add_argument("--date", help="Date in YYYY-MM-DD format (or 'yesterday')")

    args = parser.parse_args()
    
    base_path = os.path.abspath("neon_agent_copy.xlsx")
    if not os.path.exists(base_path):
        print(f"Error: {base_path} not found.")
        sys.exit(1)

    handler = NeonHandler(base_path)

    if args.command == "status":
        target_date = datetime.date.today()
        if args.date:
            if args.date == "yesterday":
                target_date = target_date - datetime.timedelta(days=1)
            else:
                try:
                    target_date = datetime.datetime.strptime(args.date, "%Y-%m-%d").date()
                except ValueError:
                    print("Error: Invalid date format. Use YYYY-MM-DD")
                    sys.exit(1)

        print(f"Checking status for {target_date} in sheet '{args.sheet}'...")
        row = handler.find_date_row(sheet_name=args.sheet, target_date=target_date)
        if row:
            print(f"Found today at Row {row}")
            vals = handler.get_row_values(args.sheet, row)
            headers = handler.get_headers(args.sheet)
            
            for c, val in vals.items():
                name = next((k for k,v in headers.items() if v == c), f"Col {c}")
                print(f"  {name}: {val}")
        else:
            print("Today's row not found.")

    elif args.command == "log":
        row = handler.find_date_row(sheet_name=args.sheet)
        if not row:
            print("Error: Could not find today's row to log to.")
            sys.exit(1)
        
        # Resolve column
        col_idx = None
        if args.column.isdigit():
            col_idx = int(args.column)
        else:
            headers = handler.get_headers(args.sheet)
            col_idx = headers.get(args.column)
            if not col_idx:
                col_idx = headers.get(args.column.lower())
        
        if col_idx:
            handler.update_cell(args.sheet, row, col_idx, args.value)
            print(f"Successfully logged '{args.value}' to Row {row}, Col {col_idx} ({args.column})")
        else:
            print(f"Error: Column '{args.column}' not found in headers.")
            print("Available headers:", list(headers.keys())[:10], "...")

    elif args.command == "inspect":
        print(f"Inspecting '{args.sheet}'...")
        headers = handler.get_headers(args.sheet, row=1)
        print("Headers (Row 1):")
        for k, v in headers.items():
            print(f"  {v}: {k}")
            
    elif args.command == "points":
        target_date = datetime.date.today()
        if args.date:
            if args.date == "yesterday":
                target_date = target_date - datetime.timedelta(days=1)
            else:
                try:
                    target_date = datetime.datetime.strptime(args.date, "%Y-%m-%d").date()
                except ValueError:
                    print(f"Error: Invalid date format: {args.date}. Use YYYY-MM-DD")
                    sys.exit(1)
        
        print(f"Pulling points for {target_date} from '0₦'...")
        row = handler.find_date_row(sheet_name='0₦', target_date=target_date)
        if row:
            vals = handler.get_row_values('0₦', row)
            headers = handler.get_headers('0₦', row=1)
            
            print(f"--- Points for {target_date} ---")
            found_any = False
            for c, val in vals.items():
                if c > 3: # Skip metadata cols
                     name = next((k for k,v in headers.items() if v == c), f"Col {c}")
                     if val is not None and val != 0 and val != ".":
                         print(f"  {name}: {val}")
                         found_any = True
            if not found_any:
                print("  (No points recorded)")
        else:
            print(f"Date {target_date} not found in '0₦'.")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
