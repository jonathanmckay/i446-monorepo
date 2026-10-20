#!/bin/zsh
# relogin.sh — full re-auth cycle for the AppFolio lease-signer daemon.
#
# Wraps relogin.py with the daemon lifecycle it needs around it: the daemon
# (lease-signerd) and an interactive relogin both launch Chrome against the
# SAME persistent profile (~/.config/m5x2/appfolio_browser_state) — running
# them concurrently trips a ProcessSingleton lock error (hit live
# 2026-09-09, mid-incident, which is its own lesson in not debugging this
# by hand under time pressure). This script owns: stop daemon -> clear
# stale locks -> run the interactive 2FA relay -> restart daemon.
#
# Must run ON ix (the daemon's home, and where the persistent browser
# profile lives) — via `ssh ix zsh relogin.sh` from elsewhere, or directly
# if already on ix.
#
# The 2FA code itself is NOT this script's job to obtain: it reads it from
# stdin. The intended caller is Claude Code, driving this over an open SSH
# session, reading the actual SMS via iMessage (sender +14695475524, a
# 6-digit code) instead of asking the user to relay it by hand -- see the
# docstring in relogin.py. Bare-manual fallback: run this yourself and type
# the code you got by text.
#
# Usage:
#   ssh ix zsh ~/i446-monorepo/tools/m5x2-automations/relogin.sh
#   (prints CODE_SENT, then blocks on stdin for the 6-digit code)

set -e
cd "$(dirname "$0")"

echo "Stopping lease-signerd..." >&2
launchctl remove com.jm.lease-signerd 2>/dev/null || true
sleep 1

echo "Clearing stale Chrome profile locks..." >&2
rm -f ~/.config/m5x2/appfolio_browser_state/Singleton* 2>/dev/null || true

echo "Running relogin..." >&2
python3 relogin.py
status=$?

echo "Restarting lease-signerd..." >&2
launchctl load ~/Library/LaunchAgents/com.jm.lease-signerd.plist 2>/dev/null || true

exit $status
