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
model = (d.get('model') or {}).get('display_name','')
cw = d.get('context_window') or {}
pct = cw.get('used_percentage')
parts = [x for x in (title, model, f'{int(pct)}% ctx' if pct is not None else '') if x]
print(' · '.join(parts))
PY
