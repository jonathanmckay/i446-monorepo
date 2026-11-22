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
# Reserved verb (2026-11-17): `noop` on line 1 of the trigger does nothing. It is
# the resting state, because install-ios-deploy-sync.sh kickstarts on any
# non-empty trigger and a stale scheme list would redeploy.
if [ "$(head -1 "$TRIGGER" 2>/dev/null)" = noop ]; then
  echo "=== noop $(date '+%F %T') ===" > "$LOG"
  exit 0
fi
# Reserved verb (2026-11-21): `wear [install|test [HH:MM]|pair <code>]` deploys
# the Wear OS watch app instead (mobile/neg1n-watch/deploy-wear.sh).
read -r VERB REST < "$TRIGGER" 2>/dev/null
if [ "$VERB" = wear ]; then
  # shellcheck disable=SC2086
  LOG="$LOG" exec /bin/zsh "$HOME/i446-monorepo/mobile/neg1n-watch/deploy-wear.sh" $REST
fi
# Reserved verb (2026-11-21): `open obsidian://...` opens a vault note in
# Obsidian on this Mac, so an Ix session can put a doc on JM's screen.
# Obsidian links only: anything else is refused, this is not a general `open`.
if [ "$VERB" = open ]; then
  case "$REST" in
    obsidian://open\?vault=vault\&file=*) /usr/bin/open "$REST"; RC=$? ;;
    *) RC=refused ;;
  esac
  echo "=== open $REST rc=$RC $(date '+%F %T') ===" > "$LOG"
  exit 0
fi
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
