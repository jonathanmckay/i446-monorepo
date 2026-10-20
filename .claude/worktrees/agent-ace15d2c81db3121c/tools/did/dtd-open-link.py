#!/usr/bin/env python3
"""dtd-open-link.py: open a task's link in the task's Chrome profile (dtd ctrl-l).

    dtd-open-link.py <task_id> <cache_file> [header_file]

2026-10-13: "if there is a link in the task, can you show it as (link) and
then that link should open the url in chrome, in the profile related to the
task: (m5x2, i9, everything else goes to 个)". The list shows every link as
(link); this opens the first one. Profile by domain label: m5x2 -> m5c7.com,
i9 -> MSFT, anything else -> 个 (same directories as ibx0's _CHROME_PROFILES).

Clicking the (link) can't work when dtd runs on ix: mosh does not carry OSC 8
hyperlinks to cmux. So this runs Chrome on straylight over ssh when it's
called on ix, and locally otherwise. The result goes to dtd's header file.
"""
from __future__ import annotations

import json
import re
import shlex
import socket
import subprocess
import sys

PROFILES = {"m5x2": ("Profile 11", "m5c7.com"), "i9": ("Profile 1", "MSFT")}
DEFAULT_PROFILE = ("Profile 5", "个")
STRAYLIGHT = "straylight-refit"
_URL = re.compile(r"https?://[^\s)\]>]+")


def find_task(cache: dict, task_id: str) -> dict | None:
    for v in cache.values():
        if isinstance(v, list):
            for t in v:
                if isinstance(t, dict) and str(t.get("id")) == task_id:
                    return t
    return None


def first_url(task: dict) -> str | None:
    m = _URL.search(task.get("content") or "") or _URL.search(task.get("description") or "")
    return m.group(0) if m else None


def profile_for(task: dict) -> tuple[str, str]:
    labels = task.get("labels") or []
    for dom, prof in PROFILES.items():
        if dom in labels:
            return prof
    return DEFAULT_PROFILE


def open_cmd(url: str, profile_dir: str) -> list[str]:
    return ["open", "-na", "Google Chrome", "--args", f"--profile-directory={profile_dir}", url]


def on_ix() -> bool:
    return socket.gethostname().lower().split(".")[0] == "ix"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: dtd-open-link.py <task_id> <cache_file> [header_file]")
        return 2
    task_id, cache_file = argv[0], argv[1]
    hdr = argv[2] if len(argv) > 2 else None

    def say(msg: str) -> None:
        if hdr:
            try:
                with open(hdr, "w") as f:
                    f.write(msg + "\n")
            except OSError:
                pass
        print(msg)

    try:
        cache = json.load(open(cache_file))
    except (OSError, ValueError) as e:
        say(f"✗ link: cache unreadable ({e})")
        return 1
    task = find_task(cache, task_id)
    url = first_url(task) if task else None
    if not url:
        say("no link on this task")
        return 1
    pdir, pname = profile_for(task)
    cmd = open_cmd(url, pdir)
    if on_ix():
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", STRAYLIGHT,
               " ".join(shlex.quote(c) for c in cmd)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception as e:  # noqa: BLE001
        say(f"✗ link: {e}")
        return 1
    if r.returncode != 0:
        say(f"✗ link: {(r.stderr or r.stdout).strip()[:120]}")
        return 1
    say(f"🔗 opened in Chrome · {pname}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
