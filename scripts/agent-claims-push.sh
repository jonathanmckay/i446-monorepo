#!/bin/zsh
# agent-claims-push.sh — mirror ~/vault/z_ibx/agent-claims to Ix, where dtd runs.
# Syncthing took ~55s to carry a claim (measured 2026-10-05), too slow for a
# per-turn working/idle signal; this rsync over the multiplexed ssh lands in
# well under a second. The folder is in .stignore on both hosts so Syncthing
# never races it. No-op on Ix itself. Always backgrounded by its callers.
#
# Lands in Ix's remote/<host>/, never Ix's root (2026-10-25): Ix sessions claim
# into the root directly, and `--delete` into the root wiped them on every
# Straylight hook fire ("you ran /notes but didn't claim the task from dtd":
# claimed 07:03:34, gone 07:03:45). --delete is scoped to this host's subtree,
# so deletions here still propagate and Ix's own claims can't be touched.
# agent_claims.load()/pane_numbers() and dtd.sh merge root + remote/*/.
[[ -r "$HOME/.claude/.host-name" && "$(<"$HOME/.claude/.host-name")" == ix ]] && exit 0
D="$HOME/vault/z_ibx/agent-claims"
[[ -d "$D" ]] || exit 0
host="${(L)$(hostname -s)}"
[[ -n "$host" ]] || exit 0
exec rsync -a --delete --exclude=/remote/ --exclude='*.tmp' --exclude='*.agents' \
  --rsync-path="mkdir -p vault/z_ibx/agent-claims/remote/$host && rsync" \
  -e "ssh -o BatchMode=yes -o ConnectTimeout=5" \
  "$D/" "ix:vault/z_ibx/agent-claims/remote/$host/" >/dev/null 2>&1
