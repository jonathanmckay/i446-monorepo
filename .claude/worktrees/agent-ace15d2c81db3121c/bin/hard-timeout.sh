#!/usr/bin/env bash
# hard-timeout.sh — portable wall-clock kill wrapper (no GNU coreutils on
# this box). Runs a command; if it's still alive after N seconds, kills the
# whole process group.
#
# Built 2026-09-03: gen_email_stats.py (cron, unattended) got stuck retrying
# Microsoft Teams auth via agency/azureauth, which defaults to a 15-MINUTE
# interactive-prompt timeout per attempt with no silent-only mode. Each retry
# spawned a fresh `agency mcp teams` + `azureauth` pair and popped a new
# login.microsoftonline.com browser window roughly every ~1-2 min for as long
# as it kept retrying. Nothing in the script or in azureauth itself caps how
# long an unattended run can hang, so a hard external kill is the only
# reliable backstop.
#
# Usage: hard-timeout.sh <seconds> <cmd> [args...]

set -u
SECS="${1:?usage: hard-timeout.sh <seconds> <cmd> [args...]}"
shift

# Run in its own process group so we can kill the whole tree, not just the
# direct child (agency/azureauth are grandchildren).
set -m
"$@" &
CMD_PID=$!
set +m

(
  sleep "$SECS"
  if kill -0 "$CMD_PID" 2>/dev/null; then
    echo "[$(date '+%F %T')] hard-timeout: killing PID $CMD_PID ($*) after ${SECS}s" >> "$HOME/.hard-timeout.log"
    kill -KILL -- -"$CMD_PID" 2>/dev/null
    kill -KILL "$CMD_PID" 2>/dev/null
    pkill -KILL -P "$CMD_PID" 2>/dev/null
  fi
) &
WATCHER_PID=$!

wait "$CMD_PID" 2>/dev/null
RC=$?
kill "$WATCHER_PID" 2>/dev/null
wait "$WATCHER_PID" 2>/dev/null
exit "$RC"
