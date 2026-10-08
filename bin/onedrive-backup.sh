#!/bin/bash
# onedrive-backup.sh — nightly cloud→disk copy of personal OneDrive onto the
# Ix Time Machine drive (2026-10-07). Time Machine only sees the ~27G of
# OneDrive that is downloaded on Ix; this pulls all ~150G straight from the
# cloud so Ix's internal disk never has to hold it, and gives a copy that
# survives losing the Microsoft account. Files deleted or overwritten in
# OneDrive are moved to a dated folder, never dropped.
# One-time setup: `rclone config` remote named onedrive-personal (OneDrive, personal).
set -u
export PATH="/opt/homebrew/bin:$PATH"
DRIVE="/Volumes/Ix Backup"
DEST="$DRIVE/OneDrive-Personal"
LOG="$HOME/.cache/onedrive-backup/$(date +%F).log"
mkdir -p "$(dirname "$LOG")"
alert() { "$HOME/i446-monorepo/bin/cron-alert.sh" onedrive-backup "$1" "$2"; }
[ -d "$DRIVE" ] || { alert "drive not mounted" "$DRIVE missing; plug the backup SSD into Ix"; exit 1; }
rclone listremotes | grep -q '^onedrive-personal:$' || { alert "rclone remote missing" "run rclone config on Ix (remote onedrive-personal)"; exit 1; }
rclone sync onedrive-personal: "$DEST" \
  --backup-dir "$DRIVE/OneDrive-Personal-changed/$(date +%F)" \
  --transfers 4 --checkers 8 --fast-list --log-level INFO --log-file "$LOG"
rc=$?
[ $rc -eq 0 ] || alert "rclone sync failed (rc=$rc)" "see $LOG on Ix"
exit $rc
