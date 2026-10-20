#!/bin/zsh
# agent-claims-push.sh — mirror ~/vault/z_ibx/agent-claims to Ix, where dtd runs.
# Syncthing took ~55s to carry a claim (measured 2026-10-05), too slow for a
# per-turn working/idle signal; this rsync over the multiplexed ssh lands in
# well under a second. The folder is in .stignore on both hosts so Syncthing
# never races it. No-op on Ix itself. Always backgrounded by its callers.
[[ -r "$HOME/.claude/.host-name" && "$(<"$HOME/.claude/.host-name")" == ix ]] && exit 0
D="$HOME/vault/z_ibx/agent-claims"
[[ -d "$D" ]] || exit 0
exec rsync -a --delete -e "ssh -o BatchMode=yes -o ConnectTimeout=5" \
  "$D/" ix:vault/z_ibx/agent-claims/ >/dev/null 2>&1
