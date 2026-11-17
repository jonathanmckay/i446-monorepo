#!/bin/bash
# Build + install inkwell-ios app(s) to imago-c. Fired by the com.jm.ios-deploy
# LaunchAgent (WatchPaths on the trigger file) so it runs in the GUI login
# session where codesign can reach the login keychain. Trigger file line 1 =
# space-separated scheme list (default "janus dtd").
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
DEVICE=00008160-000435C93EE00036
DIR="$HOME/i446-monorepo/mobile/inkwell-ios"
# TRIGGER/LOG overridable: com.jm.ios-deploy-sync (scripts/install-ios-deploy-sync.sh,
# 2026-11-08) points both into Syncthing'd ~/files/i447 so Ix sessions can
# deploy without inbound ssh to Straylight (blocked since 2026-10-28).
TRIGGER="${TRIGGER:-$HOME/.cache/jm/ios-deploy-trigger}"
LOG="${LOG:-$HOME/.cache/jm/ios-deploy.log}"
# Reserved verbs (2026-11-17): line 1 of the trigger may name a fixed triage
# command instead of a scheme list, so an Ix session can see and file a
# download sitting in Straylight's ~/Downloads without inbound ssh.
#   triage-scan   dry run; logs only the .zip entries in ~/Downloads
#   triage-apply  triage.py --source downloads --apply (config/triage.json)
#   noop          nothing; the resting state, so a re-fire never deploys
# Same ssh-localhost hop as tools/triage/com.mckay.triage.plist (launchd has no
# TCC access to ~/Downloads). The log is written whole, ending in "done rc=N".
VERB=$(head -1 "$TRIGGER" 2>/dev/null)
case "$VERB" in
  triage-scan|triage-apply|noop)
    HOP="/usr/bin/ssh -o BatchMode=yes -o ConnectTimeout=10 localhost"
    TRIAGE="/usr/bin/python3 i446-monorepo/tools/triage/triage.py --source downloads"
    {
      echo "=== $VERB start $(date '+%F %T') ==="
      rc=0
      case "$VERB" in
        triage-scan)
          $HOP "$TRIAGE --json" | /usr/bin/python3 -c '
import json, os, sys
for d in json.load(sys.stdin):
    if d["path"].lower().endswith(".zip"):
        print(os.path.basename(d["path"]), d["size"], d["added"], d["rule"], d["action"], sep=" | ")'
          rc=${PIPESTATUS[0]} ;;
        triage-apply)
          $HOP "$TRIAGE --apply" | grep -E '^downloads: (applied|.*skipped)'
          rc=${PIPESTATUS[0]} ;;
      esac
      echo "=== $VERB done rc=$rc ==="
    } > "$LOG.tmp" 2>&1
    mv "$LOG.tmp" "$LOG"
    exit 0 ;;
esac
cd "$DIR" || exit 1
SCHEMES=$(head -1 "$TRIGGER" 2>/dev/null)
[ -z "$SCHEMES" ] && SCHEMES="janus dtd"
{
  echo "=== deploy start: schemes=[$SCHEMES] ==="
  /opt/homebrew/bin/xcodegen generate || echo "xcodegen WARN"
  for s in $SCHEMES; do
    echo "--- build $s ---"
    if xcodebuild -project Inkwell.xcodeproj -scheme "$s" -destination "generic/platform=iOS" \
         -allowProvisioningUpdates -derivedDataPath build/DerivedData build 2>&1 \
         | grep -iE "errSec|CodeSign failed|BUILD SUCCEEDED|BUILD FAILED|error:"; then :; fi
    echo "--- install $s ---"
    xcrun devicectl device install app --device "$DEVICE" \
      "build/DerivedData/Build/Products/Debug-iphoneos/$s.app" 2>&1 \
      | grep -iE "App installed|bundleID|error|failed" || echo "install: no output"
  done
  echo "=== deploy done ==="
} > "$LOG" 2>&1
