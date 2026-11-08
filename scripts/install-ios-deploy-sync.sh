#!/bin/bash
# Run ONCE on Straylight. Installs a launchd agent that runs the inkwell-ios
# deploy (mobile/inkwell-ios/deploy.sh) whenever ~/files/i447/ios-deploy-trigger.txt
# changes. Ix sessions write the scheme list there (Syncthing carries it over)
# and read the result from ~/files/i447/ios-deploy.log, because inbound ssh to
# Straylight is blocked since 2026-10-28. Same pattern as install-ix-clip.sh.
set -euo pipefail
DIR="$HOME/files/i447"
TRIG="$DIR/ios-deploy-trigger.txt"
LOGF="$DIR/ios-deploy.log"
DEPLOY="$HOME/i446-monorepo/mobile/inkwell-ios/deploy.sh"
PL="$HOME/Library/LaunchAgents/com.jm.ios-deploy-sync.plist"
mkdir -p "$DIR"; touch "$TRIG"
cat > "$PL" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.jm.ios-deploy-sync</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DEPLOY</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>TRIGGER</key><string>$TRIG</string>
    <key>LOG</key><string>$LOGF</string>
  </dict>
  <key>WatchPaths</key><array><string>$TRIG</string></array>
  <key>ThrottleInterval</key><integer>10</integer>
</dict></plist>
PLIST
launchctl bootout "gui/$(id -u)/com.jm.ios-deploy-sync" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PL"
# A trigger written before install would otherwise wait for the next change.
[ -s "$TRIG" ] && launchctl kickstart "gui/$(id -u)/com.jm.ios-deploy-sync"
echo "installed: Ix writes to $TRIG deploy to the phone; log at $LOGF"
