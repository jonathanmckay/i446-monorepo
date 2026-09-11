#!/bin/bash
DIR="$HOME/Dropbox/Screenshots"
latest=$(/bin/ls -t "$DIR"/*.png 2>/dev/null | head -1)
[ -z "$latest" ] && exit 0
age=$(($(date +%s) - $(stat -f %m "$latest")))
[ "$age" -gt 5 ] && exit 0
sleep 0.3
/usr/bin/osascript -e "set the clipboard to (read (POSIX file \"$latest\") as «class PNGf»)"
