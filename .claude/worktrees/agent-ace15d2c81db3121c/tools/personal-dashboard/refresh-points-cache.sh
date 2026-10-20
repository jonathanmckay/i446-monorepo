#!/bin/bash
# refresh-points-cache.sh — Refresh the points JSON cache from Neon Excel.
# The actual cache-building logic lives in refresh_points_cache.py
# (extracted 2026-09-08 — see that file's docstring for why, including why
# it reads via xlwings/live Excel rather than opening the file directly).
#
# No pre-emptive "save workbook" step: that was only needed when this read
# the file's on-disk formula cache (openpyxl); xlwings reads the live
# in-memory values straight from the already-open workbook, so a save
# first buys nothing — and the osascript call to trigger it hung
# indefinitely under launchd/cron (a separate, unresolvable-headless
# Automation permission prompt for controlling Excel), which is worse than
# useless. Removed 2026-09-08.
#
# Cron: */30 * * * * bash ~/i446-monorepo/tools/personal-dashboard/refresh-points-cache.sh

set -euo pipefail
cd "$(dirname "$0")"

python3 refresh_points_cache.py
