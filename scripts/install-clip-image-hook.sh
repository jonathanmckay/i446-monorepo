#!/bin/bash
# Run ONCE on Ix (the machine where Claude Code runs). Registers
# scripts/claude-remote-image-hook.py as a UserPromptSubmit hook in
# ~/.claude/settings.json so a screenshot pasted in cmux on Straylight
# (/var/folders/.../T/clipboard-*.png, which does not exist on Ix) is read
# from the Syncthing copy in ~/files/i447/clips/ instead. Counterpart of
# install-clip-image-sync.sh (Straylight side). Idempotent. Claude cannot
# run this itself: editing its own settings.json is blocked as self-modification.
set -euo pipefail
python3 - <<'PY'
import json, os, shutil
p = os.path.expanduser('~/.claude/settings.json')
d = json.load(open(p))
cmd = ('d=$(cat); case "$d" in *var/folders/*clipboard-*) printf %s "$d" | '
       'python3 ~/i446-monorepo/scripts/claude-remote-image-hook.py 2>/dev/null;; esac')
ups = d.setdefault('hooks', {}).setdefault('UserPromptSubmit', [])
if any('claude-remote-image-hook' in h.get('command', '') for e in ups for h in e.get('hooks', [])):
    print('already registered'); raise SystemExit
shutil.copy2(p, p + '.bak')
ups.append({"matcher": "", "hooks": [{"type": "command", "command": cmd, "timeout": 60}]})
with open(p, 'w') as f:
    json.dump(d, f, indent=2); f.write('\n')
print('registered; backup at', p + '.bak', '— open /hooks once or restart claude to load it')
PY
