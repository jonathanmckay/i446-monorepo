#!/usr/bin/env python3
"""git-branch-guard-hook — Claude Code PreToolUse hook (Bash) that blocks branch
switches inside the shared, Syncthing-mirrored checkouts (~/vault and the
i446-monorepo inside it).

Why (2026-10-24): a session ran `git checkout -b ios-nits-2026-10-22` in Ix's
monorepo checkout. A checkout there rewrites the live working tree that
Syncthing mirrors to every machine, and autopush follows HEAD, so 173
snapshots went to that branch for two days. Branch work belongs in
`git worktree add <dir outside ~/vault> ...` (what the ship scripts do).

Blocks: `git switch …`, and `git checkout …` without a ` -- ` pathspec
(a pathspec form restores files, not branches), when the directory git runs
in (`-C path`, a preceding `cd path &&`, else the session cwd) is inside the
shared tree. Exit 2 + stderr = Claude Code refuses the call and shows why.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

SHARED = [os.path.realpath(os.path.expanduser("~/vault")),
          os.path.realpath(os.path.expanduser("~/i446-monorepo"))]


def _inside_shared(d: str) -> bool:
    try:
        r = os.path.realpath(os.path.expanduser(d))
    except Exception:
        return False
    return any(r == s or r.startswith(s + os.sep) for s in SHARED)


def blocked(command: str, cwd: str) -> str | None:
    """Reason string if `command` switches branches in the shared tree, else None."""
    cur = cwd
    for seg in re.split(r"&&|\|\||;|\n", command):
        try:
            toks = shlex.split(seg)
        except ValueError:
            toks = seg.split()
        if not toks:
            continue
        if toks[0] == "cd" and len(toks) > 1:
            t = toks[1]
            # `cd $WT`: can't expand a shell variable here; an unknown target is
            # not assumed to be the shared tree (worktree scripts do exactly this).
            cur = ("/nonexistent-unresolved" if "$" in t
                   else t if os.path.isabs(os.path.expanduser(t)) else os.path.join(cur, t))
            continue
        if toks[0] != "git":
            continue
        d, i = cur, 1
        while i < len(toks) and toks[i].startswith("-"):
            if toks[i] == "-C" and i + 1 < len(toks):
                t = os.path.expanduser(toks[i + 1])
                d = ("/nonexistent-unresolved" if "$" in t
                     else t if os.path.isabs(t) else os.path.join(cur, t))
                i += 2; continue
            if toks[i] in ("-c",) and i + 1 < len(toks):
                i += 2; continue
            i += 1
        if i >= len(toks):
            continue
        sub, rest = toks[i], toks[i + 1:]
        switching = sub == "switch" or (sub == "checkout" and "--" not in rest and rest)
        if switching and _inside_shared(d):
            return (f"Blocked: `git {sub}` in the shared checkout {d}. It is Syncthing-mirrored and "
                    "autopush follows HEAD (2026-10-24 incident: 173 snapshots went to a stray branch). "
                    "Do branch work in a worktree outside ~/vault: "
                    "`git worktree add --detach /private/tmp/<name> origin/wip`, commit there, "
                    "`git push origin HEAD:wip`, then `git worktree remove`.")
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    reason = blocked((data.get("tool_input") or {}).get("command", ""), data.get("cwd") or os.getcwd())
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
