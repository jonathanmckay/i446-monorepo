#!/bin/bash
# Fired by launchd (com.mckay.screenshot-clipboard) on every change to
# ~/files/screenshots: copy a just-taken screenshot to the clipboard, then give it
# the date-first monofolder name (2026-10-27_082047--ss.png) so the folder
# sorts chronologically (vault i446-photos-and-screenshots, 2026-10-27).
DIR="$HOME/files/screenshots"
latest=$(/bin/ls -t "$DIR"/*.png 2>/dev/null | head -1)
[ -z "$latest" ] && exit 0
age=$(($(date +%s) - $(stat -f %m "$latest")))
[ "$age" -gt 5 ] && exit 0
sleep 0.3
/usr/bin/osascript -e "set the clipboard to (read (POSIX file \"$latest\") as «class PNGf»)"
case "$(basename "$latest")" in
  Screenshot*|"Screen Shot"*)
    stamp=$(date -r "$(stat -f %m "$latest")" +%Y-%m-%d_%H%M%S)
    target="$DIR/${stamp}--ss.png"
    i=2
    while [ -e "$target" ]; do target="$DIR/${stamp}--ss-$i.png"; i=$((i+1)); done
    mv "$latest" "$target"
    # Push the screenshot straight to Ix (2026-11-20): Syncthing takes 7-15 s,
    # a paste into an Ix Claude session follows in 2-3 s. Only here, on the
    # first run for a screenshot: the rename above retriggers this job and the
    # second run falls through the case. Synchronous and last, because launchd
    # kills a job's background children when the script exits; the alarm bounds
    # a hung ssh. rsync writes a dot-temp then renames, so Ix never reads a
    # partial file. One result line per screenshot goes to a synced log that
    # Ix reads (scripts/claude-remote-image-hook.py picks the file up).
    if [ "$(hostname -s | tr '[:upper:]' '[:lower:]')" != "ix" ]; then
      plog="$HOME/files/i447/shot-push.log"
      t0=$(/usr/bin/perl -MTime::HiRes=time -e 'printf "%d", time*1000')
      err=$(/usr/bin/perl -e 'alarm 8; exec @ARGV' /usr/bin/rsync -t \
        -e "ssh -o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=2 -o ControlMaster=auto -o ControlPath=$HOME/.ssh/cm-shot-%C -o ControlPersist=900" \
        "$target" ix:.cache/shots-direct/ 2>&1 >/dev/null)
      rc=$?
      t1=$(/usr/bin/perl -MTime::HiRes=time -e 'printf "%d", time*1000')
      echo "$(date '+%Y-%m-%d %H:%M:%S') $(basename "$target") rc=$rc ms=$((t1 - t0)) $(echo "$err" | tr '\n' ' ' | cut -c1-160)" >> "$plog" 2>/dev/null
      if [ -f "$plog" ] && [ "$(wc -l < "$plog")" -gt 400 ]; then
        tail -200 "$plog" > "$plog.tmp" && mv -f "$plog.tmp" "$plog"
      fi
    fi
    ;;
esac
