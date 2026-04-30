#!/usr/bin/env bash
# ix-osa.sh — run AppleScript on the remote `ix` host (the only Mac
# allowed to write to the canonical Excel workbook). Refuses to fall
# back to local osascript; local writes cause OneDrive merge conflicts
# against the ix-driven copy of the workbook.
#
# Usage:
#   ix-osa.sh                # script on stdin
#   ix-osa.sh <<<"$script"   # script as a here-string
#   echo "$script" | ix-osa.sh
#
# Env:
#   IX_HOST   ssh alias / host (default: ix)
#   IX_DEBUG  if set, print the command and stderr verbatim
#   IX_QUEUE  if set to "1", queue the script on exit 3 instead of just failing
#
# Exit codes:
#   0  — AppleScript ran on ix and returned a non-ERROR string
#   2  — AppleScript ran on ix but returned ERROR:/ERR: (logic failure)
#   3  — ssh transport failed (ix unreachable / auth / network)
#   4  — usage error (e.g. no script on stdin)
#
# The helper NEVER calls local /usr/bin/osascript. Writing to the local
# Excel copy is forbidden by policy.

set -u
set -o pipefail

readonly IX_HOST="${IX_HOST:-ix}"
readonly UNREACHABLE_MSG="ERROR: ix unreachable — write aborted to prevent OneDrive merge conflict. Restore SSH to ix and retry."

# Read the AppleScript from stdin.
if [ -t 0 ]; then
    echo "ix-osa.sh: no AppleScript on stdin" >&2
    echo "usage: $0 <<<\"\$script\"  (or pipe via heredoc)" >&2
    exit 4
fi
script="$(cat)"
if [ -z "${script//[[:space:]]/}" ]; then
    echo "ix-osa.sh: empty AppleScript on stdin" >&2
    exit 4
fi

if [ -n "${IX_DEBUG:-}" ]; then
    echo "ix-osa.sh: ssh ${IX_HOST} osascript -" >&2
fi

# Execute. ssh exit codes 255 == transport error; otherwise we get
# osascript's own exit code on the remote.
out="$(ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
       "${IX_HOST}" osascript - <<<"$script" 2>/tmp/ix-osa.stderr.$$)"
rc=$?
err="$(cat /tmp/ix-osa.stderr.$$ 2>/dev/null)"
rm -f /tmp/ix-osa.stderr.$$

if [ "$rc" -eq 255 ]; then
    echo "$UNREACHABLE_MSG" >&2
    [ -n "$err" ] && [ -n "${IX_DEBUG:-}" ] && echo "ssh: $err" >&2
    # Queue the write for later replay if IX_QUEUE is set
    if [ "${IX_QUEUE:-}" = "1" ]; then
        queue_file="${HOME}/.claude/ix-write-queue.jsonl"
        python3 -c "
import json, sys, datetime
entry = {'ts': datetime.datetime.now().isoformat(), 'script': sys.argv[1]}
print(json.dumps(entry))
" "$script" >> "$queue_file" 2>/dev/null
        echo "QUEUED: write saved to ix-write-queue.jsonl ($(wc -l < "$queue_file" | tr -d ' ') pending)" >&2
    fi
    exit 3
fi

# Print whatever the remote produced before judging it.
[ -n "$out" ] && printf '%s\n' "$out"
[ -n "$err" ] && [ -n "${IX_DEBUG:-}" ] && printf '%s\n' "$err" >&2

if [ "$rc" -ne 0 ]; then
    # osascript itself raised (compile error, etc.)
    [ -n "$err" ] && [ -z "${IX_DEBUG:-}" ] && printf '%s\n' "$err" >&2
    exit 2
fi

# AppleScript templates conventionally return strings prefixed with
# OK:/ERROR:/SKIP:. Treat ERROR / ERR as a logic failure.
trimmed="$(printf '%s' "$out" | awk 'NF{print; exit}')"
case "$trimmed" in
    ERROR:*|ERR:*) exit 2 ;;
esac

exit 0
