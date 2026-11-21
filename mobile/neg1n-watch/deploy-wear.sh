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
    log)     adb logcat -d -s Neg1n | tail -"${1:-15}" ;;
    *)       echo "✗ unknown wear verb: $verb"; false ;;
  esac
  echo "=== wear $verb exit=$? $(date '+%F %T') ==="
} > "${LOG:-/dev/stdout}" 2>&1
