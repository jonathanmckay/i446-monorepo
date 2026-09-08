#!/bin/bash
# refresh-points-cache.sh — Refresh the points JSON cache from Neon Excel.
# Pair with a periodic "save workbook" AppleScript to keep formula caches
# fresh. The actual cache-building logic lives in refresh_points_cache.py
# (extracted 2026-09-08 — see that file's docstring for why).
# Cron: */30 * * * * bash ~/i446-monorepo/tools/personal-dashboard/refresh-points-cache.sh

set -euo pipefail
cd "$(dirname "$0")"

# First, tell Excel to save (flushes formula caches to disk)
osascript -e 'tell application "Microsoft Excel" to save workbook "Neon分v12.2.xlsx"' 2>/dev/null || true
sleep 2

python3 refresh_points_cache.py
