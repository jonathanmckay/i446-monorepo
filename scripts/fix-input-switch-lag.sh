#!/bin/bash
# Run ONCE on Straylight (or any Mac where you type Pinyin). Disables the
# input-source indicator bubble that macOS shows at the cursor on every
# language switch. Since Sonoma the switch only completes once that bubble
# is gone, so the first keystrokes after Caps Lock / Ctrl-Space land in the
# old source ("20fe分" instead of "20分"). Source:
# https://chimit.me/posts/macos-input-source-switch-lag/
# Takes effect after log out / log in. Idempotent. `--undo` restores the bubble.
# Ix cannot run this for you: inbound ssh to Straylight is blocked since
# 2026-10-28, and Ix has no Chinese input source anyway.
set -euo pipefail
DOMAIN="${INPUT_SWITCH_DOMAIN:-kCFPreferencesAnyApplication}"   # test override
KEY=TSMLanguageIndicatorEnabled
if [ "${1:-}" = "--undo" ]; then
  defaults delete "$DOMAIN" "$KEY" 2>/dev/null || true
  echo "restored: indicator bubble re-enabled (log out / in to apply)"
  exit 0
fi
defaults write "$DOMAIN" "$KEY" -int 0
v=$(defaults read "$DOMAIN" "$KEY")
[ "$v" = "0" ] || { echo "failed: $KEY=$v" >&2; exit 1; }
echo "$KEY=0: input-source indicator off. Log out and back in, then retest 20分."
