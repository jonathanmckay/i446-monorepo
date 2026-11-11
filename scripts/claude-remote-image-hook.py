#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook (Ix): rewrite pasted-image paths from Straylight.

cmux on Straylight saves a pasted image to Straylight's per-user temp dir
(/var/folders/<x>/<hash>/T/clipboard-<stamp>.png) and inserts that path into
the terminal. When the terminal is an ssh session into Ix, the file is not
here. scripts/clip-image-sync.sh on Straylight copies such files into
~/files/i447/clips/ (Syncthing), and this hook tells Claude to read the synced
copy instead. Waits up to WAIT_S for Syncthing to deliver it.

Hooks cannot edit the prompt, only add context, so the context is imperative.
"""
import json
import os
import re
import sys
import time

CLIPS_DIR = os.path.expanduser(os.environ.get("CLIP_DST", "~/files/i447/clips"))
WAIT_S = float(os.environ.get("CLIP_WAIT_S", "45"))
STEP_S = 0.5

PATH_RE = re.compile(
    r"(?:/private)?/var/folders/[^/\s]+/[^/\s]+/T/clipboard-[^/\s\"'`]+?\.(?:png|jpe?g|gif|webp)",
    re.I,
)


def remote_image_paths(prompt, exists=os.path.exists):
    """Pasted-image paths in the prompt that do not exist on this machine."""
    out = []
    for m in PATH_RE.finditer(prompt or ""):
        p = m.group(0)
        if p not in out and not exists(p):
            out.append(p)
    return out


def synced_path(remote, clips_dir=CLIPS_DIR):
    return os.path.join(clips_dir, os.path.basename(remote))


def in_flight(local):
    """Syncthing writes <dir>/.syncthing.<name>.tmp while a file is arriving."""
    d, b = os.path.split(local)
    return os.path.exists(os.path.join(d, f".syncthing.{b}.tmp"))


def wait_for(local, wait_s=WAIT_S, step=STEP_S, now=time.monotonic, sleep=time.sleep):
    deadline = now() + wait_s
    while True:
        if os.path.isfile(local) and os.path.getsize(local) > 0:
            return True
        if now() >= deadline and not in_flight(local):
            return False
        sleep(step)


def build_context(prompt, exists=os.path.exists, clips_dir=CLIPS_DIR, waiter=wait_for):
    """Return the additionalContext string, or '' when the prompt needs nothing."""
    remotes = remote_image_paths(prompt, exists)
    if not remotes:
        return ""
    lines = []
    for r in remotes:
        local = synced_path(r, clips_dir)
        if waiter(local):
            lines.append(
                f"The pasted image {r} is a Straylight path and does not exist on this "
                f"machine. The synced copy is {local}. Use Read on {local}; do not try {r}."
            )
        else:
            lines.append(
                f"The pasted image {r} is a Straylight path and does not exist on this "
                f"machine, and no synced copy arrived at {local} within {int(WAIT_S)} s. "
                "Tell the user the screenshot did not sync; the Straylight agent is "
                "installed by scripts/install-clip-image-sync.sh and Syncthing must be "
                "watching ~/files."
            )
    return "\n".join(lines)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    ctx = build_context(data.get("prompt", ""))
    if ctx:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit", "additionalContext": ctx}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
