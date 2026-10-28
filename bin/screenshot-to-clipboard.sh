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
    ;;
esac
