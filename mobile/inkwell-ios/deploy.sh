#!/bin/bash
# Build + install inkwell-ios app(s) to imago-c. Fired by the com.jm.ios-deploy
# LaunchAgent (WatchPaths on the trigger file) so it runs in the GUI login
# session where codesign can reach the login keychain. Trigger file line 1 =
# space-separated scheme list (default "janus dtd").
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
DEVICE=00008160-000435C93EE00036
DIR="$HOME/i446-monorepo/mobile/inkwell-ios"
TRIGGER="$HOME/.cache/jm/ios-deploy-trigger"
LOG="$HOME/.cache/jm/ios-deploy.log"
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
