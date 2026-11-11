#!/bin/bash
# Run ONCE on Straylight. Installs a launchd agent that runs
# scripts/clip-image-sync.sh every 2 s, copying images cmux saves on paste
# (per-user temp dir, clipboard-*.png) into ~/files/i447/clips/. Syncthing
# carries them to Ix, where the Claude UserPromptSubmit hook
# scripts/claude-remote-image-hook.py rewrites the dead Straylight path to the
# synced copy. Inbound ssh to Straylight is blocked since 2026-10-28, so this
# follows the install-ix-clip.sh pattern. Idempotent.
set -euo pipefail
SCRIPT="$HOME/i446-monorepo/scripts/clip-image-sync.sh"
DST="$HOME/files/i447/clips"
LABEL="com.jm.clip-image-sync"
PL="$HOME/Library/LaunchAgents/$LABEL.plist"
[ -f "$SCRIPT" ] || { echo "missing $SCRIPT (is the repo synced?)" >&2; exit 1; }
mkdir -p "$DST" "$(dirname "$PL")"
cat > "$PL" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$SCRIPT</string></array>
  <key>StartInterval</key><integer>2</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
</dict></plist>
PLIST
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PL"
echo "installed: pasted images land in $DST within ~2 s"
# Syncthing must watch the files folder, or the copy waits for the hourly rescan.
if command -v curl >/dev/null; then
  cfg=$(curl -s -m 3 http://localhost:8384/rest/config/folders/files \
        -H "X-API-Key: $(sed -n 's/.*<apikey>\(.*\)<\/apikey>.*/\1/p' "$HOME/Library/Application Support/Syncthing/config.xml" 2>/dev/null | head -1)" 2>/dev/null || true)
  case "$cfg" in
    *'"fsWatcherEnabled":true'*) echo "syncthing: fsWatcher on for 'files'" ;;
    "") echo "warn: could not query Syncthing on :8384; check that folder 'files' has Watch for Changes on" ;;
    *) echo "WARN: Syncthing folder 'files' has fsWatcher OFF; enable Watch for Changes or clips arrive only on rescan" ;;
  esac
fi
