#!/bin/zsh
# agent-claims-pull.sh — mirror Ix's own claim store into this host's
# remote/ix/, the reverse of agent-claims-push.sh. Run by dtd's watcher on
# every host but Ix, detached, every few seconds.
#
# 2026-11-02: "/1-1n claimed its card but it sits at #20 in dtd" — the session
# ran on Ix and claimed into Ix's store, which Syncthing ignores (.stignore) and
# push never sends anywhere (no-op on Ix; Straylight blocks inbound ssh). So
# Straylight's dtd never saw Ix claims. It pulls them over its outbound ssh;
# load() and dtd.sh already merge remote/*/, so they float to the top here too.
#
# Only Ix's root: its remote/ holds this host's own pushed copy. panes.json is
# skipped (cmux runs here; this host's cs map is the live one) and by-session/
# is per-host hook state no reader needs.
[[ -r "$HOME/.claude/.host-name" && "$(<"$HOME/.claude/.host-name")" == ix ]] && exit 0
D="$HOME/vault/z_ibx/agent-claims"
[[ -d "$D" ]] || exit 0
# One pull at a time: a slow ssh must not stack up behind the 2s cadence. The
# lock lives outside $D: touching $D's mtime would make dtd reload every cycle.
L="${TMPDIR:-/tmp}/agent-claims-pull.lock"
mkdir "$L" 2>/dev/null || {
  zmodload zsh/stat zsh/datetime 2>/dev/null
  (( EPOCHSECONDS - $(zstat +mtime "$L" 2>/dev/null || echo 0) > 30 )) || exit 0
}
trap 'rmdir "$L" 2>/dev/null' EXIT
mkdir -p "$D/remote/ix"
rsync -a --delete --exclude=/remote/ --exclude=/by-session/ --exclude=/panes.json \
  --exclude='*.tmp' --exclude='*.agents' \
  -e "ssh -o BatchMode=yes -o ConnectTimeout=5" \
  "${AGENT_CLAIMS_PULL_SRC:-ix:vault/z_ibx/agent-claims/}" "$D/remote/ix/" >/dev/null 2>&1
