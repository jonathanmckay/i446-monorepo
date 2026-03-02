import pandas as pd
from openpyxl import load_workbook
import datetime
import os
import json

class NeonHandler:
    def __init__(self, file_path):
        self.file_path = file_path
        self.wb = None
        self._load()

    def _load(self):
        # We assume formulas need preservation
        self.wb = load_workbook(self.file_path, data_only=False)

    def find_date_row(self, sheet_name='0s', target_date=None):
        if target_date is None:
            target_date = datetime.datetime.now().date()
        
        # Try using cached map if available and sheet is 0s
        if sheet_name == '0s':
            try:
                map_path = os.path.join(os.path.dirname(__file__), 'date_map.json')
                if os.path.exists(map_path):
                    with open(map_path, 'r') as f:
                        date_map = json.load(f)
                    target_str = target_date.strftime("%Y-%m-%d")
                    if target_str in date_map:
                        return date_map[target_str]
            except Exception as e:
                print(f"Error reading date map: {e}")

        try:
            # Fallback to scanning (data_only=True)
            read_wb = load_workbook(self.file_path, data_only=True)
            if sheet_name not in read_wb.sheetnames:
                 return None
            sheet = read_wb[sheet_name]
            
            # Scan Col 2 (B) for dates (or Col 3 for 0N)
            # 0s = Col 2
            # 0N = Col 3
            col_idx = 3 if sheet_name == '0₦' else 2
            
            # Limit search
            limit = 1000
            for r in range(1, limit):
                val = sheet.cell(row=r, column=col_idx).value
                if isinstance(val, datetime.datetime):
                    if val.date() == target_date:
                        read_wb.close()
                        return r
                # Handle string dates just in case
                elif isinstance(val, str):
                    try:
                        # minimal parsing assumption YYYY-MM-DD
                        dt = datetime.datetime.strptime(val, "%Y-%m-%d") # strict
                        if dt.date() == target_date:
                             read_wb.close()
                             return r
                    except:
                        pass
            read_wb.close()
        except Exception as e:
            print(f"Error searching date: {e}")
        return None

    def get_headers(self, sheet_name='0s', row=1):
        headers = {}
        if sheet_name not in self.wb.sheetnames:
            return headers
        sheet = self.wb[sheet_name]
        for c in range(1, 40):
            val = sheet.cell(row=row, column=c).value
            if val:
                s_val = str(val).strip()
                headers[s_val] = c
                # Also index by lowercase for easier matching
                headers[s_val.lower()] = c
        return headers

    def update_cell(self, sheet_name, row, col_idx, value):
        sheet = self.wb[sheet_name]
        sheet.cell(row=row, column=col_idx).value = value
        self.wb.save(self.file_path)
        return True

    def get_row_values(self, sheet_name, row):
        # Read with data_only=True for display
        wb_read = load_workbook(self.file_path, data_only=True)
        sheet = wb_read[sheet_name]
        values = {}
        for c in range(1, 40): # Read up to 40 cols
            values[c] = sheet.cell(row=row, column=c).value
        wb_read.close()
        return values
