#!/bin/bash
# Run ONCE on Straylight. Installs a launchd agent that copies
# ~/files/i447/ix-clip.txt to the clipboard whenever it changes.
# Ix sessions write that file (Syncthing carries it over) because inbound
# ssh to Straylight is blocked since 2026-10-28 and Terminal ignores OSC 52.
set -euo pipefail
CLIP="$HOME/files/i447/ix-clip.txt"
PL="$HOME/Library/LaunchAgents/com.jm.ix-clip.plist"
mkdir -p "$(dirname "$CLIP")"; touch "$CLIP"
cat > "$PL" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.jm.ix-clip</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string>-c</string><string>/usr/bin/pbcopy &lt; "$CLIP"</string></array>
  <key>WatchPaths</key><array><string>$CLIP</string></array>
  <key>ThrottleInterval</key><integer>1</integer>
</dict></plist>
PLIST
launchctl bootout "gui/$(id -u)/com.jm.ix-clip" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PL"
echo "installed: Ix writes to $CLIP land in this clipboard"
