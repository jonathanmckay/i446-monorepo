#!/bin/bash
# Nightly copy of the Neon ledger DB (Neon off Excel, Durability) — Ix cron 04:20,
# after the 04:05 rules diff. Time Machine on Ix already includes ~/.local/share;
# this is the second, Syncthing-carried copy in ~/files/i447/neon-backups.
#
#   VACUUM INTO a .tmp, then mv (no torn file if the job dies mid-write);
#   keep the newest 14; alert via bin/cron-alert.sh on failure.
export PATH="/opt/homebrew/bin:/usr/bin:/bin"
DB="$HOME/.local/share/neon/neon.db"
DEST="$HOME/files/i447/neon-backups"
LOGDIR="$HOME/.cache/neon-rules"; mkdir -p "$LOGDIR" "$DEST"
LOG="$LOGDIR/db-backup.log"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }
fail() { log "FAILED: $2"; "$HOME/bin/cron-alert.sh" neon_db_backup "$1" "$2 (see $LOG)"; exit 1; }

[ -f "$DB" ] || fail "no_db" "$DB missing"
STAMP="$(date +%Y-%m-%d)"
OUT="$DEST/neon-$STAMP.db"
rm -f "$OUT.tmp"
if ! /usr/bin/sqlite3 "$DB" "VACUUM INTO '$OUT.tmp'"; then
    rm -f "$OUT.tmp"; fail "vacuum_failed" "VACUUM INTO failed"
fi
/usr/bin/sqlite3 "$OUT.tmp" "PRAGMA integrity_check" | grep -q '^ok$' || { rm -f "$OUT.tmp"; fail "integrity" "backup failed integrity_check"; }
mv -f "$OUT.tmp" "$OUT"
log "ok $OUT $(stat -f %z "$OUT") bytes"
# prune: keep the newest 14
ls -1t "$DEST"/neon-*.db 2>/dev/null | tail -n +15 | while read -r f; do rm -f "$f"; log "pruned $f"; done
