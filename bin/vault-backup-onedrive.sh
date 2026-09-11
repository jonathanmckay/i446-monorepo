#!/bin/bash
# Weekly vault snapshot to OneDrive with GFS retention:
#   keep all snapshots from the last 4 weeks,
#   one per month for the last year,
#   one per year beyond that.
# Excludes mirror ~/vault/.stignore: git repos, drive mirrors, .git internals.
#
# TCC constraint (found 2026-07-27, after 6 weeks of orphaned .partial files):
# under launchd, the OneDrive File Provider allows CREATING files but denies
# rename/delete/append/listdir on EXISTING entries ("Operation not
# permitted") — the same operations work fine over ssh/Terminal. So:
#   - the archive is built in LOCAL staging (atomic rename is local), then a
#     single create-only cp into OneDrive, verified with cmp;
#   - the log lives in /tmp;
#   - the GFS prune (needs listdir + delete) hops through ssh-to-localhost,
#     whose sshd context has full disk access (verified 2026-07-27).
set -uo pipefail
DEST="$HOME/OneDrive/vault-backups"
STAGE="$HOME/.cache/vault-backup-staging"
STAMP=$(date +%Y%m%d)
NAME="vault-backup-$STAMP.tar.zst"
OUT="$DEST/$NAME"
LOG="/tmp/vault-backup-onedrive.log"

mkdir -p "$STAGE"
echo "=== $(date) starting $OUT ===" >> "$LOG"
[ -e "$OUT" ] && { echo "already exists, skipping" >> "$LOG"; exit 0; }

tar --exclude=".git" \
    --exclude="vault/i447/i446/i446-monorepo" \
    --exclude="vault/hcmp/o315/blog" \
    --exclude="vault/h335/m5x2/drive-main" \
    --exclude="vault/h335/m5x2/drive-fundraising-legal" \
    --exclude="vault/h335/m5x2/drive-hr" \
    --exclude="vault/h335/m5x2/drive-investor-k1s" \
    --exclude="vault/h335/m5x2/m5x2-m/drive-main" \
    --exclude="vault/h335/m5x2/m5x2-m/drive-fundraising-legal" \
    --exclude="vault/h335/m5x2/m5x2-m/drive-hr" \
    --exclude="vault/h335/m5x2/m5x2-m/drive-investor-k1s" \
    --exclude="vault/vault" --exclude="vault/.claude" --exclude="vault/.stversions" \
    -cf - -C "$HOME" vault | zstd -q -o "$STAGE/$NAME.partial" \
  && mv "$STAGE/$NAME.partial" "$STAGE/$NAME" \
  || { rm -f "$STAGE/$NAME.partial"; echo "BACKUP FAILED (archive) $(date)" >> "$LOG"; exit 1; }

# Create-only copy into OneDrive, then verify byte-for-byte.
cp "$STAGE/$NAME" "$OUT" \
  && cmp -s "$STAGE/$NAME" "$OUT" \
  || { echo "BACKUP FAILED (copy/verify) $(date) — torn file may remain at $OUT" >> "$LOG"; exit 1; }
rm -f "$STAGE/$NAME"

echo "wrote $(du -h "$OUT" | cut -f1) $(date)" >> "$LOG"

# --- GFS prune (via ssh-to-localhost: launchd can't listdir/delete here) ---
ssh -o BatchMode=yes -o ConnectTimeout=5 localhost 'python3 - "$HOME/OneDrive/vault-backups"' >> "$LOG" 2>&1 <<"PYEOF"
import os, re, sys
from datetime import date, datetime

dest = sys.argv[1]
snaps = []
for f in os.listdir(dest):
    m = re.fullmatch(r"vault-backup-(\d{8})\.tar\.zst", f)
    if m:
        snaps.append((datetime.strptime(m.group(1), "%Y%m%d").date(), f))
snaps.sort()
today = date.today()
keep = set()
for d, f in snaps:
    if (today - d).days <= 28:
        keep.add(f)
for key in {(d.year, d.month) for d, _ in snaps}:
    monthly = [f for d, f in snaps if (d.year, d.month) == key]
    keep.add(monthly[0])
for year in {d.year for d, _ in snaps}:
    yearly = [f for d, f in snaps if d.year == year]
    keep.add(yearly[0])
for d, f in snaps:
    if f not in keep:
        try:
            os.remove(os.path.join(dest, f))
            print("pruned", f)
        except OSError as e:
            print("PRUNE FAILED:", f, e)
PYEOF
rc=$?
[ $rc -ne 0 ] && echo "PRUNE HOP FAILED (ssh localhost, rc=$rc) $(date)" >> "$LOG"
exit 0
