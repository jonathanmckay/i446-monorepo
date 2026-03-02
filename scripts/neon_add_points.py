#!/usr/bin/env python3
"""Add points to Neon分v11.xlsx for today's date."""

import xlwings as xw
from datetime import datetime
import sys

FILE_PATH = "/Users/mckay/Library/CloudStorage/OneDrive-Personal/Old + Documents/Neon分v11.xlsx"
SHEET = "0₦"
DATE_COL = "C"
COLUMN_MAP = {
    "代": "X",
    "代码": "X",
    "m5x2": "BE",
}

def add_points(column_name: str, points: int):
    # Get column letter
    col = COLUMN_MAP.get(column_name, column_name)
    
    app = xw.App(visible=False)
    try:
        wb = app.books.open(FILE_PATH)
        ws = wb.sheets[SHEET]
        
        # Find today's row
        today = datetime.now().date()
        for row in range(5, 400):
            cell_val = ws.range(f"{DATE_COL}{row}").value
            if isinstance(cell_val, datetime) and cell_val.date() == today:
                target_cell = ws.range(f"{col}{row}")
                current = target_cell.value or 0
                target_cell.value = current + points
                print(f"Added {points} to {col}{row} (was {current}, now {current + points})")
                wb.save()
                return
        print(f"Could not find today's date ({today})")
    finally:
        wb.close()
        app.quit()

if __name__ == "__main__":
    col = sys.argv[1] if len(sys.argv) > 1 else "代"
    pts = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    add_points(col, pts)
