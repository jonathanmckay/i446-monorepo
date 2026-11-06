#!/usr/bin/env python3
"""Register the background-agent hooks in this host's ~/.claude/settings.json
(settings.json is per-host and untracked; Ix never had them). Idempotent.

SubagentStart/SubagentStop feed agent-claim-hook.sh's per-session agent list,
which keeps a dtd claim on ✶ rather than 🕐 while a background agent runs. The
Stop colour hook skips blue on the same check. Keeps a .bak-subagent copy."""
import json
import shutil
from pathlib import Path

CFG = Path.home() / ".claude/settings.json"
HOOK = "~/i446-monorepo/scripts/agent-claim-hook.sh"

d = json.loads(CFG.read_text())
hooks = d["hooks"]
for ev, mode in (("SubagentStart", "subagent-start"), ("SubagentStop", "subagent-stop")):
    cmd = f"{HOOK} {mode} >/dev/null 2>&1"
    if not any(cmd == h.get("command") for g in hooks.get(ev, []) for h in g.get("hooks", [])):
        hooks.setdefault(ev, []).append({"matcher": "", "hooks": [{"type": "command", "command": cmd}]})
for g in hooks.get("Stop", []):
    for h in g.get("hooks", []):
        c = h.get("command", "")
        if "term-color.sh blue" in c and f"{HOOK} busy ||" not in c:
            head, tail = c.rsplit("; ", 1)
            h["command"] = f"{head}; {HOOK} busy || {tail}"
shutil.copy(CFG, CFG.with_name(CFG.name + ".bak-subagent"))
CFG.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
print("ok:", CFG)
