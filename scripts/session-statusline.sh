#!/bin/zsh
# Claude Code statusLine: "<title> · <model> · <ctx%>" from the stdin JSON.
input=$(cat)
python3 - "$input" <<'PY' 2>/dev/null
import sys, json, os
d = json.loads(sys.argv[1])
sid = d.get('session_id','')
title = ''
p = os.path.expanduser(f'~/.local/state/jm/session-titles/{sid}')
if sid and os.path.exists(p):
    title = open(p).read().strip()
# Full tier (2026-11-14): a claimed session shows its dtd task's full name here,
# the tab shows the initial, the tmux title the short name (tools/cs/title_tiers.py).
try:
    sys.path.insert(0, os.path.expanduser('~/i446-monorepo/tools/cs'))
    import title_tiers
    t = title_tiers.tiers(sid)
    if t.get('source') == 'task' and t.get('full'):
        title = (t.get('init') + ' ' if t.get('init') else '') + t['full']
except Exception:
    pass
model = (d.get('model') or {}).get('display_name','')
cw = d.get('context_window') or {}
pct = cw.get('used_percentage')
parts = [x for x in (title, model, f'{int(pct)}% ctx' if pct is not None else '') if x]
print(' · '.join(parts))
PY
