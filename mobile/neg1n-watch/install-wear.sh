#!/bin/zsh
# Build the watch APK (Neon Watch, module :wear) and push it to the watch.
#
#   ./install-wear.sh <ip:port>     # the watch's Wireless-debugging address
#   ./install-wear.sh               # reuse a watch `adb devices` already lists
#   ./install-wear.sh pair <ip:pairport> <code> <ip:port>   # first run on a Mac
#   ./install-wear.sh test [HH:MM] [ip:port]   # no build: buzz the watch at
#                                 HH:MM today (default: 2 minutes from now)
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

# test [HH:MM] [ip:port]: schedule a test nudge on the installed app.
test_at=""
if [[ "${1:-}" == test ]]; then
  shift
  if [[ "${1:-}" =~ '^[0-9]{1,2}:[0-9]{2}$' ]]; then
    test_at=$(date -j -f "%Y-%m-%d %H:%M:%S" "$(date +%F) $1:00" +%s)
    shift
  else
    test_at=$(( $(date +%s) + 120 ))
  fi
  (( test_at > $(date +%s) + 20 )) || { echo "✗ $(date -r $test_at +%H:%M) is not in the future"; exit 1; }
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

if [[ -n "$test_at" ]]; then
  # --activity-clear-task: without it a second launch only brings the old
  # task forward and the extra never reaches onCreate.
  out=$(adb -s "$serial" shell am start --activity-clear-task \
    -n com.mckay.neg1n/com.mckay.neg1n.wear.MainActivity --el test_nudge_at "${test_at}000" 2>&1)
  [[ "$out" != *Warning* && "$out" != *Error* ]] || { echo "✗ $out"; exit 1; }
  sleep 2
  adb -s "$serial" logcat -d -t 200 | grep -q "Nudge: test armed for ${test_at}000" \
    || { echo "✗ the app did not arm the test (is this build installed? run without 'test' first)"; exit 1; }
  # Back to the watch face: a notification can stay silent under its own app.
  adb -s "$serial" shell input keyevent KEYCODE_HOME
  echo "✓ test nudge armed for $(date -r $test_at +%H:%M:%S); keep the watch on your wrist, DND/Bedtime off"
  echo "  after: adb -s $serial logcat -d | grep 'Nudge: test'"
  exit 0
fi

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
