#!/bin/bash
# refresh-points-cache.sh — Refresh the points JSON cache from Neon Excel.
# Uses openpyxl (data_only=True) which reads cached formula values from the
# last Excel save. Pair with a periodic "save workbook" AppleScript to keep
# formula caches fresh.
# Cron: */30 * * * * bash ~/i446-monorepo/tools/personal-dashboard/refresh-points-cache.sh

set -euo pipefail
CACHE="$(dirname "$0")/.points-cache.json"
NEON="$HOME/OneDrive/vault-excel/Neon分v12.2.xlsx"

# First, tell Excel to save (flushes formula caches to disk)
osascript -e 'tell application "Microsoft Excel" to save workbook "Neon分v12.2.xlsx"' 2>/dev/null || true
sleep 2

python3 -c "
import openpyxl, json
from datetime import datetime, date, timedelta
wb = openpyxl.load_workbook('$NEON', data_only=True, read_only=True)
ws = wb['0分']
COLS = {16: '-1₦', 17: '0₲', 18: 'i9', 19: 'm5', 20: '个', 21: '媒', 22: '思', 23: 'hcb', 24: 'xk', 25: '社'}
today = date.today()
cutoff = today - timedelta(days=90)
result = {}
for row in ws.iter_rows(min_row=3, values_only=True):
    b = row[1]
    if b is None: continue
    if isinstance(b, datetime): d = b.date()
    elif isinstance(b, date): d = b
    else: continue
    if d <= cutoff or d > today: continue
    day_data = {}
    for idx, label in COLS.items():
        val = row[idx - 1]
        if val is not None and isinstance(val, (int, float)) and val > 0:
            day_data[label] = int(round(float(val)))
    if day_data:
        result[d.isoformat()] = day_data
wb.close()
with open('$CACHE', 'w') as f:
    json.dump(result, f, indent=2)
print(f'wrote {len(result)} days to cache')
"
