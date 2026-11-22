#!/bin/zsh
# Watch deploy without a terminal on Straylight. Run by the com.jm.ios-deploy-sync
# drop box (mobile/inkwell-ios/deploy.sh hands over any trigger whose first
# word is `wear`), because the watch APK must be signed with Straylight's
# debug key and Ix cannot ssh in. From Ix:
#
#   printf 'wear\n%s\n' "$(date)" > ~/files/i447/ios-deploy-trigger.txt
#
#   wear                 build, install, then a test nudge 2 minutes out
#   wear install         build and install only
#   wear test [HH:MM]    test nudge only (installed build)
#   wear pair <code>     pair this Mac; the watch must show Pair new device
#   wear log [N]         last N (15) lines of the watch app's log
#   wear status          launch the app and print the status the watch holds
#
# The watch is found over mDNS: it only needs Wireless debugging on and the
# same Wi-Fi as Straylight. Result: the LOG file (~/files/i447/ios-deploy.log).
export PATH=/opt/homebrew/share/android-commandlinetools/platform-tools:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
cd "$(dirname "$0")" || exit 1
verb="${1:-all}"; shift 2>/dev/null
{
  echo "=== wear $verb start $(date '+%F %T') on $(hostname -s) ==="
  case "$verb" in
    all)     ./install-wear.sh && ./install-wear.sh test ;;
    install) ./install-wear.sh ;;
    test)    ./install-wear.sh test "$@" ;;
    pair)    ./install-wear.sh pair-auto "$@" ;;
    log)     adb devices | sed 1d
             serial=$(adb devices | awk 'NR>1 && $2=="device"{print $1; exit}')
             adb -s "$serial" logcat -d -s Neg1n:V | tail -n "${1:-15}" ;;
    status)  # plain launch: MainActivity logs the status the watch holds
             # (block, done/not_done, updatedAt), i.e. what a nudge would use
             serial=$(adb devices | awk 'NR>1 && $2=="device"{print $1; exit}')
             adb -s "$serial" shell am start --activity-clear-task \
               -n com.mckay.neg1n/com.mckay.neg1n.wear.MainActivity >/dev/null
             sleep 4
             adb -s "$serial" logcat -d -s Neg1n:V | grep 'DataLayerReader' | tail -n 1
             echo "  watch clock: $(adb -s "$serial" shell date +%s)000" ;;
    *)       echo "✗ unknown wear verb: $verb"; false ;;
  esac
  echo "=== wear $verb exit=$? $(date '+%F %T') ==="
} > "${LOG:-/dev/stdout}" 2>&1
