#!/bin/bash
# files-backup-onedrive — nightly cloud copy of ~/files, runs ON ix.
#
# Mirrors /Volumes/Ix Files/files to onedrive-personal:files-backup. Anything
# the mirror would delete or overwrite is moved to
# onedrive-personal:files-backup-versions/<date>/ first, so nothing is lost to
# a bad sync. `Ix Files` shares a disk with Time Machine; this is the copy
# that survives that disk dying (home-folder-tree.md, decided 2026-10-28).
#
# Launchd: com.jm.files-backup-onedrive (daily 01:30, ssh-localhost wrapper
# for Full Disk Access, same as vault-backup-onedrive).
# Success stamp: ~/.local/state/jm/files-backup-onedrive.ok (backup-health checks it).
set -euo pipefail

SRC="/Volumes/Ix Files/files"
DEST="onedrive-personal:files-backup"
VERSIONS="onedrive-personal:files-backup-versions/$(date +%F)"
STATE="$HOME/.local/state/jm"
LOG="$STATE/files-backup-onedrive.log"
LOCK="$STATE/files-backup-onedrive.lock"
mkdir -p "$STATE"

# An unmounted volume would look like an empty tree and the mirror would
# delete everything; refuse unless the volume and a known folder are present.
if ! mount | grep -q " on /Volumes/Ix Files "; then
  echo "$(date '+%F %T') Ix Files not mounted, skipping" >> "$LOG"; exit 1
fi
[ -d "$SRC/qz12" ] || { echo "$(date '+%F %T') $SRC/qz12 missing, skipping" >> "$LOG"; exit 1; }

# One run at a time (the first upload takes hours).
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date '+%F %T') previous run still going, skipping" >> "$LOG"; exit 0
fi
trap 'rmdir "$LOCK"' EXIT

echo "$(date '+%F %T') start" >> "$LOG"
rclone sync "$SRC" "$DEST" \
  --backup-dir "$VERSIONS" \
  --exclude ".DS_Store" --exclude ".stfolder/**" --exclude ".stversions/**" \
  --links --max-delete 2000 \
  --transfers 8 --checkers 16 \
  --log-file "$LOG" --log-level NOTICE --stats 30m --stats-log-level NOTICE
date '+%F %T' > "$STATE/files-backup-onedrive.ok"
echo "$(date '+%F %T') done" >> "$LOG"
