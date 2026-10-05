#!/bin/bash
# Run the AppleScript on stdin (or the -e script) with Excel events OFF.
#
# Every pipeline write to Neon goes through here (ix-osa.py/.sh, the replay
# queue, lib/neon/excel's fallback; excel-http does the same in Python), so
# the Neon audit macro's SheetChange listener only ever sees edits made in
# Excel itself and can attribute them to source 3p-app / via excel.
#
# A shared lock serializes writers: without it, writer A could re-enable
# events while writer B is mid-write and B's write would log as a manual edit.
# Events are re-enabled on every exit path, including a timeout kill.
#
# Usage: neon-osa-quiet.sh < script.applescript      (like `osascript -`)
#        neon-osa-quiet.sh -e '<script>'             (like `osascript -e`)

LOCK=/tmp/neon-osa-quiet.lock
OFF='tell application "Microsoft Excel" to set enable events to false'
ON='tell application "Microsoft Excel" to set enable events to true'

if [ "${1:-}" = "-e" ]; then
    script="$2"
else
    script="$(cat)"
fi

run() {
    osascript -e "$OFF" >/dev/null 2>&1
    trap 'osascript -e "$ON" >/dev/null 2>&1' EXIT HUP INT TERM
    osascript - <<<"$script"
}
export -f run 2>/dev/null
export OFF ON script

# lockf ships with macOS; -k keeps the lock file, -t waits up to 30s.
exec /usr/bin/lockf -k -t 30 "$LOCK" /bin/bash -c "$(declare -f run); run"
