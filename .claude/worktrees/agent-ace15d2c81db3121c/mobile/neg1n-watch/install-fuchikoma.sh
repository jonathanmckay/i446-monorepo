#!/bin/zsh
# Build the Fuchikoma APK and push it to the phone over adb.
#
#   ./install-fuchikoma.sh <ip:port>     # phone's Wireless-debugging address
#   ./install-fuchikoma.sh               # reuse whatever `adb devices` already lists
#
# The port is whatever Settings → Developer options → Wireless debugging shows
# on the phone at that moment; it changes every time the toggle flips, so it
# is an argument, not a constant. Over Tailscale the IP is fuchikoma's
# (100.110.77.24, see vault/i447/i448/hardware-roles.md) — the phone must
# have Wireless debugging ON and this Mac must already be paired with it
# once (`adb pair ip:pairport` with the on-screen code).
set -euo pipefail
cd "$(dirname "$0")"
export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@17}"

addr="${1:-}"
if [[ -n "$addr" ]]; then
  echo "→ adb connect $addr"
  out=$(adb connect "$addr" 2>&1); echo "  $out"
  [[ "$out" == *connected* ]] || { echo "✗ could not connect (is Wireless debugging on, and is this the current port?)"; exit 1; }
fi
if adb devices | awk 'NR>1 && $2=="offline"{f=1} END{exit !f}'; then
  echo "✗ adb sees the device but it is OFFLINE — accept the debugging prompt on the phone, or re-pair"; exit 1
fi
if ! adb devices | awk 'NR>1 && $2=="device"{f=1} END{exit !f}'; then
  echo "✗ no device attached — pass the phone's ip:port"; exit 1
fi

echo "→ ./gradlew :fuchikoma:assembleDebug"
./gradlew :fuchikoma:assembleDebug -q 2>&1 | grep -v "SDK processing\|SDK XML" || true
apk=fuchikoma/build/outputs/apk/debug/fuchikoma-debug.apk
[[ -s "$apk" ]] || { echo "✗ APK missing: $apk"; exit 1; }
echo "→ adb install -r $apk"
adb install -r "$apk"
echo "→ launching"
adb shell am start -n com.mckay.fuchikoma/.MainActivity >/dev/null
echo "✓ Fuchikoma installed ($(du -h "$apk" | cut -f1))"
