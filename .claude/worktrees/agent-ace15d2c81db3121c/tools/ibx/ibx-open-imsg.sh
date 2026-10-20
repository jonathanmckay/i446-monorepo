#!/usr/bin/env bash
# Opened by clicking an imsg notification — launches imsg in a new cmux tab.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

SURFACE=$(cmux new-surface --type terminal 2>/dev/null | grep -oE 'surface:[0-9]+' | head -1)
if [[ -n "$SURFACE" ]]; then
    cmux respawn-pane --surface "$SURFACE" --command "bash $HOME/i446-monorepo/tools/ibx/imsg_wrapper.sh"
fi
