#!/bin/bash
# Runs on Straylight every 2 s (launchd, see install-clip-image-sync.sh).
# cmux saves a pasted image to the per-user temp dir and inserts that path
# into the terminal. When the terminal is an ssh session into Ix, the path is
# dead there. This copies fresh clipboard-*.png/jpg into ~/files/i447/clips/,
# which Syncthing carries to Ix, where scripts/claude-remote-image-hook.py
# points Claude at the synced copy.
set -u
SRC="${CLIP_SRC:-$(getconf DARWIN_USER_TEMP_DIR)}"
DST="${CLIP_DST:-$HOME/files/i447/clips}"
mkdir -p "$DST" || exit 0
now=$(date +%s)
# -mmin -3: only recent pastes. Skip anything written in the last 2 s so a
# half-written PNG is not copied (the next tick picks it up once it settles).
find "$SRC" -maxdepth 1 -type f -iname 'clipboard-*' -mmin -3 2>/dev/null | while IFS= read -r f; do
  case "$f" in *.png|*.PNG|*.jpg|*.JPG|*.jpeg|*.JPEG|*.gif|*.GIF|*.webp|*.WEBP) ;; *) continue ;; esac
  mtime=$(stat -f %m "$f" 2>/dev/null) || continue
  [ $((now - mtime)) -lt 2 ] && continue
  b=$(basename "$f"); d="$DST/$b"
  if [ -f "$d" ] && [ "$(stat -f %z "$f")" = "$(stat -f %z "$d")" ]; then continue; fi
  cp -p "$f" "$d.part" && mv -f "$d.part" "$d"
done
# Prune clips older than 7 days so the synced folder does not grow forever.
find "$DST" -maxdepth 1 -type f -iname 'clipboard-*' -mtime +7 -delete 2>/dev/null
exit 0
