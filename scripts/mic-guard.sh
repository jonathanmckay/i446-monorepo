#!/bin/bash
# mic-guard.sh — pin the system default INPUT to the MacBook mic.
#
# Why: when AirPods connect, macOS silently makes them the default input.
# Conferencing apps on "Default" then use the AirPods mic, which forces the
# AirPods into HFP (mono/24kHz), which breaks Meet Output → BlackHole
# routing and degrades every d357 recording to one-sided mic-only capture
# (see d357/CLAUDE.md, regression 2026-06-04 Adam Habig call).
#
# Output (what you hear) is untouched — AirPods stay the output device in
# A2DP. Only the default *input* is pinned.
#
# Opt out: touch ~/.config/mic-guard.off  (remove to re-enable)
# Runs via launchd: ~/Library/LaunchAgents/com.mckay.mic-guard.plist

set -u
PIN="MacBook Pro Microphone"
SAS="/opt/homebrew/bin/SwitchAudioSource"

[ -f "$HOME/.config/mic-guard.off" ] && exit 0
[ -x "$SAS" ] || exit 0

cur="$("$SAS" -c -t input 2>/dev/null)"
if [ -n "$cur" ] && [ "$cur" != "$PIN" ]; then
    # Only pin if the MacBook mic actually exists (clamshell/external-only
    # setups won't have it; don't fight reality).
    if "$SAS" -a -t input | grep -qx "$PIN"; then
        "$SAS" -t input -s "$PIN" >/dev/null
    fi
fi
exit 0
