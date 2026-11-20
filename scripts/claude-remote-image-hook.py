#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook (Ix): rewrite pasted-image paths from Straylight.

cmux on Straylight saves a pasted image to Straylight's per-user temp dir
(/var/folders/<x>/<hash>/T/clipboard-<stamp>.png) and inserts that path into
the terminal. When the terminal is an ssh session into Ix, the file is not
here. scripts/clip-image-sync.sh on Straylight copies such files into
~/files/i447/clips/ (Syncthing), and this hook tells Claude to read the synced
copy instead.

Fallback (2026-11-20): nearly every paste is the screenshot JM just took
(bin/screenshot-to-clipboard.sh puts each one on the clipboard), and that
screenshot syncs on its own into ~/screenshots under a different name. When
no clip with the pasted name exists, the hook picks the newest screenshot
taken in the SHOT_WINDOW_S before the paste, read from the stamp in the
pasted filename. Syncthing needs 7-15 s to deliver one (often longer), so the
hook waits until GRACE_S after the paste, capped at WAIT_S from now.

Hooks cannot edit the prompt, only add context, so the context is imperative.
"""
import datetime
import json
import os
import re
import sys
import time

CLIPS_DIR = os.path.expanduser(os.environ.get("CLIP_DST", "~/files/i447/clips"))
WAIT_S = float(os.environ.get("CLIP_WAIT_S", "45"))
STEP_S = 0.5
SHOTS_DIR = os.path.expanduser(os.environ.get("CLIP_SHOTS", "~/screenshots"))
SHOT_WINDOW_S = float(os.environ.get("CLIP_SHOT_WINDOW_S", "300"))
GRACE_S = float(os.environ.get("CLIP_GRACE_S", "40"))
STAMP_RE = re.compile(r"clipboard-(\d{4})-(\d\d)-(\d\d)-(\d\d)(\d\d)(\d\d)")
IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")

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


def paste_time(remote):
    """Epoch seconds of the paste, from clipboard-YYYY-MM-DD-HHMMSS (local time)."""
    m = STAMP_RE.search(os.path.basename(remote))
    if not m:
        return None
    try:
        return datetime.datetime(*map(int, m.groups())).timestamp()
    except ValueError:
        return None


def recent_screenshot(paste_ts, shots_dir=SHOTS_DIR, window_s=SHOT_WINDOW_S):
    """Newest screenshot taken in the window before the paste, or None.

    Syncthing preserves mtime, so mtime here is when Straylight took it. The
    folder holds ~15k files: only names from the paste's day (or macOS's
    not-yet-renamed 'Screenshot ...') are stat'ed.
    """
    if paste_ts is None:
        return None
    days = {datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d")
            for t in (paste_ts, paste_ts - window_s)}
    best = None
    try:
        with os.scandir(shots_dir) as it:
            for e in it:
                n = e.name
                if n.startswith(".") or not n.lower().endswith(IMG_EXT):
                    continue
                if not (n[:10] in days or n.startswith(("Screenshot", "Screen Shot"))):
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                if st.st_size <= 0 or not (paste_ts - window_s <= st.st_mtime <= paste_ts + 2):
                    continue
                if best is None or st.st_mtime > best[0]:
                    best = (st.st_mtime, e.path)
    except OSError:
        return None
    return best


def resolve(remote, clips_dir=CLIPS_DIR, shots_dir=SHOTS_DIR, wait_s=None,
            step=STEP_S, now=time.time, sleep=time.sleep):
    """('clip', path, None) | ('shot', path, seconds_before_paste) | (None, None, None)."""
    local = synced_path(remote, clips_dir)
    pt = paste_time(remote)
    if wait_s is None:
        # Typing the prompt usually outlasts the sync; wait only for what is left.
        wait_s = WAIT_S if pt is None else max(0.0, min(WAIT_S, pt + GRACE_S - now()))
    deadline = now() + wait_s
    while True:
        if os.path.isfile(local) and os.path.getsize(local) > 0:
            return "clip", local, None
        if not in_flight(local):
            shot = recent_screenshot(pt, shots_dir)
            if shot:
                return "shot", shot[1], max(0, int(pt - shot[0]))
            if now() >= deadline:
                return None, None, None
        sleep(step)


def build_context(prompt, exists=os.path.exists, clips_dir=CLIPS_DIR, waiter=None,
                  shots_dir=SHOTS_DIR, resolver=resolve):
    """Return the additionalContext string, or '' when the prompt needs nothing."""
    remotes = remote_image_paths(prompt, exists)
    if not remotes:
        return ""
    lines = []
    for r in remotes:
        local = synced_path(r, clips_dir)
        if waiter is not None:  # legacy seam: exact-name clip only
            kind, path, age = ("clip", local, None) if waiter(local) else (None, None, None)
        else:
            kind, path, age = resolver(r, clips_dir=clips_dir, shots_dir=shots_dir)
        if kind == "clip":
            lines.append(
                f"The pasted image {r} is a Straylight path and does not exist on this "
                f"machine. The synced copy is {path}. Use Read on {path}; do not try {r}."
            )
        elif kind == "shot":
            lines.append(
                f"The pasted image {r} is a Straylight path and does not exist on this "
                f"machine. No copy under that name synced, but the screenshot taken {age} s "
                f"before the paste is {path}, which is almost certainly the same image. "
                f"Use Read on {path}; do not try {r}. If its content plainly does not fit "
                "the request, say the paste may have been a different image."
            )
        else:
            lines.append(
                f"The pasted image {r} is a Straylight path and does not exist on this "
                f"machine. No synced copy arrived at {local} and no screenshot from the "
                f"{int(SHOT_WINDOW_S)} s before the paste is in {shots_dir}. "
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
