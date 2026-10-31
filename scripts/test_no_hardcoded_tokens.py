"""No API token literals in tracked files: this repo is public on GitHub.

Dream v172 (2026-10-29) found the live Todoist token hardcoded in 23 places
(did-fast, dtd.sh, the dashboard, the crontab, a launchd plist, skill docs).
Code reads ~/.config/todoist/token (else $TODOIST_API_KEY) instead.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Token-shaped literals in a token context. 40 hex = a Todoist API token.
PATTERNS = [
    r"Bearer [0-9a-f]{40}\b",
    r"TOKEN\s*=\s*['\"][0-9a-f]{40}['\"]",
    r"token\s*=\s*['\"][0-9a-f]{40}['\"]",
    r"TODOIST_API_KEY=['\"]?[0-9a-f]{40}\b",
    r"<key>TODOIST_API_KEY</key>\s*<string>[0-9a-f]{40}</string>",
]


def _tracked_hits(pattern: str) -> list[str]:
    r = subprocess.run(["git", "-C", str(REPO), "grep", "-nIP", pattern, "--", "."],
                       capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if l.strip()]


def test_no_token_shaped_literals():
    hits = []
    for p in PATTERNS:
        hits += _tracked_hits(p)
    # report file:line only, never the secret itself
    assert not hits, "hardcoded token literal(s):\n" + "\n".join(
        h.split(":", 2)[0] + ":" + h.split(":", 2)[1] for h in hits)


def test_live_todoist_token_not_in_tree():
    f = Path.home() / ".config/todoist/token"
    if not f.exists():
        return
    tok = f.read_text().strip()
    if not re.fullmatch(r"[0-9A-Za-z]{20,}", tok):
        return
    r = subprocess.run(["git", "-C", str(REPO), "grep", "-lIF", tok, "--", "."],
                       capture_output=True, text=True)
    assert not r.stdout.strip(), "live Todoist token found in: " + r.stdout.replace("\n", " ")
