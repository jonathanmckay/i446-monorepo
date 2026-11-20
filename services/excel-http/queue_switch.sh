#!/bin/bash
# queue_switch.sh on|off|status — turn excel-http's write queue on or off.
#
# The queue (NEON_WRITE_QUEUE=1, Neon off Excel step 4b) is read once at daemon
# start, from the launchd plist's environment. This sets or removes the
# variable in the INSTALLED plist, reloads the agent and prints /health.
#
#   on      record every write before Excel is touched; replay when it answers
#   off     today's behaviour. Refused while writes are still queued: with the
#           queue off nothing replays them.
#   status  /health only
#
# Do it just after a block boundary (06:00, 08:00, …): the restart takes a few
# seconds and a write that arrives during it falls back as it does today.
set -euo pipefail
PLIST="$HOME/Library/LaunchAgents/com.mckay.excel-http.plist"
URL="http://127.0.0.1:9876/health"
PB=/usr/libexec/PlistBuddy

health() { curl -s -m 5 "$URL" || true; }
queue_field() {   # $1 = key of /health's queue block
  health | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("queue", {}).get(sys.argv[1], ""))
except Exception: print("")' "$1"
}

case "${1:-status}" in
  status)
    health; echo; exit 0 ;;
  on)
    $PB -c "Delete :EnvironmentVariables:NEON_WRITE_QUEUE" "$PLIST" 2>/dev/null || true
    $PB -c "Add :EnvironmentVariables:NEON_WRITE_QUEUE string 1" "$PLIST" ;;
  off)
    pending="$(queue_field pending)"; held="$(queue_field held)"
    if [ "${pending:-0}" != "0" ] && [ -n "$pending" ]; then
      echo "refusing: $pending write(s) still queued; they would never be applied with the queue off" >&2
      exit 1
    fi
    [ -n "$held" ] && [ "$held" != "0" ] && echo "note: $held held write(s) stay in ~/.local/share/neon/write-intents.jsonl" >&2
    $PB -c "Delete :EnvironmentVariables:NEON_WRITE_QUEUE" "$PLIST" 2>/dev/null || true ;;
  *)
    echo "usage: $0 on|off|status" >&2; exit 2 ;;
esac

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
for _ in $(seq 1 30); do
  sleep 1
  out="$(health)"
  [ -n "$out" ] && { echo "$out"; exit 0; }
done
echo "excel-http did not answer within 30 s after the reload; check ~/Library/Logs or launchctl list | grep excel-http" >&2
exit 1
