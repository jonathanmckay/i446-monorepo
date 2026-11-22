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
#   wear always on [H]  buzz at +30 in every block for H (72) hours, even when
#                        all rituals are done; wear always off ends it
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
    always)  # always on [hours=72] | always off: buzz at +30 in EVERY block,
             # also when the real nudge is silent. Expires by itself.
             serial=$(adb devices | awk 'NR>1 && $2=="device"{print $1; exit}')
             now=$(date +%s); until=0
             [[ "${1:-on}" == on ]] && until=$(( (now + ${2:-72} * 3600) * 1000 ))
             adb -s "$serial" shell am start --activity-clear-task \
               -n com.mckay.neg1n/com.mckay.neg1n.wear.MainActivity \
               --el nudge_always_until "$until" --el nudge_always_cmd_at "${now}000" >/dev/null
             sleep 4
             if adb -s "$serial" logcat -d -s Neg1n:V | grep -q "always-test until=$until (cmd=${now}000)"; then
               if (( until )); then echo "✓ always-test on until $(date -r $((until / 1000)) '+%F %H:%M')"
               else echo "✓ always-test off"; fi
             else echo "✗ the watch did not take the command (is the build with always-test installed?)"; false; fi ;;
    *)       echo "✗ unknown wear verb: $verb"; false ;;
  esac
  echo "=== wear $verb exit=$? $(date '+%F %T') ==="
} > "${LOG:-/dev/stdout}" 2>&1
