#!/bin/bash
# One-time Time Machine setup on Ix (run with sudo). 2026-10-07.
# Backs up Ix, including ~/vault as it exists on Ix, to the 2TB USB SSD
# ("Ix Backup", APFS). vault_health.py's time-machine rule checks it.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
DEST="/Volumes/Ix Backup"
[ -d "$DEST" ] || { echo "$DEST not mounted"; exit 1; }
tmutil setdestination -a "$DEST"
tmutil enable
tmutil startbackup --auto
sleep 5
tmutil destinationinfo
tmutil status | grep -E "Running|BackupPhase|Percent" || true
