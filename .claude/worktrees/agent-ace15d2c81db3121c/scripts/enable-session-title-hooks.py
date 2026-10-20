#!/usr/bin/env python3
"""One-shot: wire session-title.sh (Stop + UserPromptSubmit) and the statusline into ~/.claude/settings.json."""
import json, shutil, time
p = '/Users/mckay/.claude/settings.json'
shutil.copy(p, p + f'.bak-{time.strftime("%Y%m%d-%H%M%S")}')
s = json.load(open(p))
h = {'type': 'command', 'command': '~/i446-monorepo/scripts/session-title.sh >/dev/null 2>&1', 'async': True}
for ev in ('Stop', 'UserPromptSubmit'):
    groups = s.setdefault('hooks', {}).setdefault(ev, [])
    if not any('session-title.sh' in x.get('command', '') for g in groups for x in g.get('hooks', [])):
        groups.append({'matcher': '', 'hooks': [h]})
s['statusLine'] = {'type': 'command', 'command': '~/i446-monorepo/scripts/session-statusline.sh'}
json.dump(s, open(p, 'w'), indent=2, ensure_ascii=False); open(p, 'a').write('\n')
print('ok: hooks + statusLine wired; takes effect on the next claude session')
