#!/usr/bin/env bash
# Refresh the m5x2 occupancy dashboard: pull fresh AppFolio data, rebuild index.html.
# --daily 7 re-pulls the last 7 days every run, so any missed day self-heals
# (no more 6/15→6/22 gaps). Idempotent per date. Run by cron daily; also safe
# to run by hand: `bash refresh.sh`.
set -euo pipefail
cd "$(dirname "$0")"
echo "── refresh $(date '+%Y-%m-%d %H:%M:%S') ──"
python3 fetch.py --daily 7
python3 build.py
echo "── done ──"
