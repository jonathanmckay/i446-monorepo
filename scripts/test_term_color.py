"""Regression tests for the orange-sticky protocol in term-color.sh.

Bug: after a tool-use failure set the terminal orange, the Stop hook
unconditionally ran `term-color.sh blue`, overwriting the signal before
the user could see it. Fix: orange writes a sticky marker; Stop hook
skips blue while the marker exists; UserPromptSubmit clears it.
"""

import json
import os
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
SCRIPT = HOME / "i446-monorepo" / "scripts" / "term-color.sh"
SETTINGS = HOME / ".claude" / "settings.json"
FLAG = "/tmp/claude-orange-sticky"


def _settings_hooks(event):
    data = json.loads(SETTINGS.read_text())
    groups = data["hooks"].get(event, [])
    return [h["command"] for g in groups for h in g["hooks"] if h["type"] == "command"]


def test_term_color_orange_touches_sticky_flag():
    body = SCRIPT.read_text()
    assert FLAG in body, "term-color.sh must reference the sticky flag path"
    assert 'COLOR" = "orange"' in body, "orange branch must set the flag"


def test_stop_hook_guards_blue_with_sticky_flag():
    cmds = _settings_hooks("Stop")
    blue_cmds = [c for c in cmds if "term-color.sh blue" in c]
    assert blue_cmds, "Stop hook should still have a blue command"
    for c in blue_cmds:
        assert FLAG in c, f"Stop-blue command must guard on {FLAG}: {c}"


def test_user_prompt_submit_clears_sticky_flag():
    cmds = _settings_hooks("UserPromptSubmit")
    assert any(f"rm -f {FLAG}" in c for c in cmds), \
        "UserPromptSubmit must clear the sticky flag so new turns start clean"
