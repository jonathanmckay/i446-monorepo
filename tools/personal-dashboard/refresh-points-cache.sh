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
from openpyxl.utils import column_index_from_string as ci
wb = openpyxl.load_workbook('$NEON', data_only=True, read_only=True)
today = date.today()
cutoff = today - timedelta(days=90)
result = {}

def as_date(v):
    if isinstance(v, datetime): return v.date()
    if isinstance(v, date): return v
    return None

# --- 0分: per-domain points + block breakdown + day grand total (col D) ---
# Read by neg1n's /api/day-points (__total__, quarter-circle arc complication).
ws = wb['0分']
COLS = {16: '-1₦', 17: '0₲', 18: 'i9', 19: 'm5', 20: '个', 21: '媒', 22: '思', 23: 'hcb', 24: 'xk', 25: '社'}
BLOCK_COLS = {7: '卯', 8: '辰', 9: '巳', 10: '午', 11: '未', 12: '申', 13: '酉', 14: '戌', 15: '亥'}
TOTAL_COL = ci('D')
for row in ws.iter_rows(min_row=3, values_only=True):
    d = as_date(row[1])
    if d is None or d <= cutoff or d > today: continue
    day_data = result.setdefault(d.isoformat(), {})
    for idx, label in COLS.items():
        val = row[idx - 1]
        if val is not None and isinstance(val, (int, float)) and val > 0:
            day_data[label] = int(round(float(val)))
    block_data = {}
    for idx, label in BLOCK_COLS.items():
        val = row[idx - 1]
        if val is not None and isinstance(val, (int, float)) and val > 0:
            block_data[label] = int(round(float(val)))
    if block_data:
        day_data['__block__'] = block_data
    total = row[TOTAL_COL - 1]
    if isinstance(total, (int, float)):
        day_data['__total__'] = int(round(float(total)))

# --- hcbi: calories eaten (col U) + today's hcbp+hcbc score (Y+AA), per
# day, for neg1n's /api/hcb. Own date column (B), independent row numbering
# from 0分/0n. hcbp+hcbc was originally read as the fixed Q2+Q3 running
# total (hcbi!X375+X378) but that's a year-scale figure incompatible with
# the 131 goal, which is a DAILY target — JM: '=hcbi!AA{row}+hcbi!Y{row}' is
# the actual per-day figure to use (2026-09-08).
ws = wb['hcbi']
KCAL_COL = ci('U')
Y_COL = ci('Y')
AA_COL = ci('AA')
for row in ws.iter_rows(min_row=3, values_only=True):
    d = as_date(row[1])
    if d is None or d <= cutoff or d > today: continue
    day_data = result.setdefault(d.isoformat(), {})
    kcal = row[KCAL_COL - 1]
    if isinstance(kcal, (int, float)) and kcal > 0:
        day_data['__hcb_kcal__'] = int(round(float(kcal)))
    hcbp_hcbc = sum(v for v in (row[Y_COL - 1], row[AA_COL - 1]) if isinstance(v, (int, float)))
    day_data['__hcbp_hcbc__'] = int(round(float(hcbp_hcbc)))

# --- 0n: prayer count (ص) + hcmp minutes (其他人+冥想+o314), per day ---
# for neg1n's /api/hcmp. Own date column (C).
ws = wb['0n']
SALAT_COL = ci('AP')
HCMP_COLS = [ci('AQ'), ci('AR'), ci('AS')]  # o314, 冥想, 其他人
for row in ws.iter_rows(min_row=5, values_only=True):
    d = as_date(row[2])
    if d is None or d <= cutoff or d > today: continue
    day_data = result.setdefault(d.isoformat(), {})
    salat = row[SALAT_COL - 1]
    if isinstance(salat, (int, float)):
        day_data['__salat__'] = int(round(float(salat)))
    hcmp_min = sum(row[c - 1] for c in HCMP_COLS if isinstance(row[c - 1], (int, float)))
    day_data['__hcmp_min__'] = int(round(float(hcmp_min)))  # 0 is a real value, not missing data

wb.close()
with open('$CACHE', 'w') as f:
    json.dump(result, f, indent=2)
print(f'wrote {len(result)} days to cache')
"
