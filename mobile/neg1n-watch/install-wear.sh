#!/bin/zsh
# Build the watch APK (Neon Watch, module :wear) and push it to the watch.
#
#   ./install-wear.sh <ip:port>     # the watch's Wireless-debugging address
#   ./install-wear.sh               # reuse a watch `adb devices` already lists
#   ./install-wear.sh pair <ip:pairport> <code> <ip:port>   # first run on a Mac
#
# On the watch: Settings → Developer options → Wireless debugging. The port
# changes whenever the toggle flips, and the first time this Mac must be
# paired (`adb pair ip:pairport` with the on-screen code). The watch and
# this Mac must be on the same Wi-Fi. Only the watch is reinstalled: the
# 30-minute nudge needs nothing new on the phone.
set -euo pipefail
cd "$(dirname "$0")"
export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@17}"
# Use the SDK's own adb (sdkmanager "platform-tools"). The Homebrew cask copy
# is quarantined and hangs on a Gatekeeper prompt in a headless session.
export PATH="/opt/homebrew/share/android-commandlinetools/platform-tools:$PATH"

# First time from a given Mac: ./install-wear.sh pair <ip:pairport> <code> <ip:port>
# (watch: Wireless debugging → Pair new device shows the pair port and code;
# the main Wireless debugging screen shows the connect port).
if [[ "${1:-}" == pair ]]; then
  [[ $# -eq 4 ]] || { echo "usage: $0 pair <ip:pairport> <code> <ip:port>"; exit 1; }
  echo "→ adb pair $2"
  out=$(adb pair "$2" "$3" 2>&1); echo "  $out"
  [[ "$out" == *Successfully* ]] || { echo "✗ pairing failed (code expired? pair port changed?)"; exit 1; }
  shift 3
fi

addr="${1:-}"
if [[ -n "$addr" ]]; then
  echo "→ adb connect $addr"
  out=$(adb connect "$addr" 2>&1); echo "  $out"
  [[ "$out" == *connected* ]] || { echo "✗ could not connect (Wireless debugging on? current port?)"; exit 1; }
fi
serial="${addr:-$(adb devices | awk 'NR>1 && $2=="device"{print $1; exit}')}"
[[ -n "$serial" ]] || { echo "✗ no device attached — pass the watch's ip:port"; exit 1; }
kind=$(adb -s "$serial" shell getprop ro.build.characteristics | tr -d '\r')
[[ "$kind" == *watch* ]] || { echo "✗ $serial is not a watch ($kind) — this APK is the wear module"; exit 1; }

echo "→ ./gradlew :wear:testDebugUnitTest :wear:assembleDebug"
./gradlew :wear:testDebugUnitTest :wear:assembleDebug -q 2>&1 | grep -v "SDK processing\|SDK XML" || true
apk=wear/build/outputs/apk/debug/wear-debug.apk
[[ -s "$apk" && "$apk" -nt wear/src/main/AndroidManifest.xml ]] || { echo "✗ build failed or APK is stale: $apk"; exit 1; }
echo "→ adb install -r $apk"
adb -s "$serial" install -r "$apk"
# Notifications are a runtime permission from Wear OS 4; grant it here so the
# first nudge does not depend on a tap. Harmless on older watches.
adb -s "$serial" shell pm grant com.mckay.neg1n android.permission.POST_NOTIFICATIONS 2>/dev/null || true
# Opening the app arms the alarm (a fresh install has none until first run).
adb -s "$serial" shell am start -n com.mckay.neg1n/com.mckay.neg1n.wear.MainActivity >/dev/null
echo "✓ Neon Watch installed ($(du -h "$apk" | cut -f1)); nudge armed"
echo "  check: adb -s $serial logcat -d | grep Neg1n"
