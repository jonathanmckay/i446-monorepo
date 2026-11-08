#!/bin/bash
# Run ONCE on Straylight. Installs an hourly launchd agent that runs
# scripts/claude-perm-sync.py --host straylight from the synced repo, so
# Straylight merges its own Claude permission allowlist with Ix's through
# scripts/.perm-sync/ in the vault-synced repo (Syncthing). Replaces the Ix job's ssh into
# Straylight, blocked since 2026-10-28. Same pattern as install-ix-clip.sh.
# Idempotent: re-running rewrites the plist and reloads the agent.
set -euo pipefail
REPO="$HOME/i446-monorepo"
SCRIPT="$REPO/scripts/claude-perm-sync.py"
LOGF="$REPO/scripts/.claude-perm-sync-straylight.log"
LABEL="com.mckay.claude-perm-sync"  # same label as the Ix job
PL="$HOME/Library/LaunchAgents/$LABEL.plist"
[ -f "$SCRIPT" ] || { echo "missing $SCRIPT (is the repo synced?)" >&2; exit 1; }
mkdir -p "$REPO/scripts/.perm-sync" "$(dirname "$PL")"
cat > "$PL" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/usr/bin/python3</string><string>$SCRIPT</string><string>--host</string><string>straylight</string></array>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOGF</string>
  <key>StandardErrorPath</key><string>$LOGF</string>
</dict></plist>
PLIST
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PL"
echo "installed: Straylight syncs Claude permissions hourly via scripts/.perm-sync; log at $LOGF"
