"""Optimistic row-hide for forms that dtd opens inline (2026-11-12).

Bug: "for tasks that require a survey (-1g, 0l) I see the task at the top
of the list flash before it disappears". The -1g / 0g / 0l forms save in a
DETACHED child (did-fast closes the card seconds later), so when fzf
repainted after the form exited, nothing had told dtd's list builder to
drop the row yet; it lingered until the child's cache refresh landed.

dtd's own completions avoid this by appending the task id to
`$DTD_REMOVED.ids`; the list builder skips any row whose id is in that file
(see the removed_ids check in tools/did/dtd.sh). The form wrapper
($DTD_FORM) exports DTD_TASK_ID and DTD_HIDE_IDS_FILE so a form can do the
same the moment it knows the card WILL be closed, and undo it if the close
then fails. Both are no-ops outside dtd (env unset), so the forms behave
unchanged from a plain shell.

Rules for callers:
  * hide_card() BEFORE forking the detached child, so the child's unhide
    can never run ahead of the hide;
  * unhide_card() on every failure of the did-fast close -- nonzero rc,
    `todoist.closed` false, AND timeouts/exceptions (run_bounded raises
    TimeoutExpired, the historical 0l failure mode) -- or the card stays
    hidden all session while Todoist still shows it open.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ENV_ID = "DTD_TASK_ID"
ENV_FILE = "DTD_HIDE_IDS_FILE"


def _target() -> tuple[str, Path] | None:
    tid = (os.environ.get(ENV_ID) or "").strip()
    f = (os.environ.get(ENV_FILE) or "").strip()
    if not tid or not f:
        return None
    return tid, Path(f)


def did_fast_closed(stdout: str) -> bool:
    """True iff did-fast's JSON stdout says the Todoist card was closed.
    `--ritual <tag>` reports top-level todoist.closed; a habit run (`0l`,
    `0g`) reports results[].todoist.closed. Anything unparseable is False:
    an unconfirmed close must UNhide, never leave a live card hidden."""
    try:
        data = json.loads(stdout or "")
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    td = data.get("todoist")
    if isinstance(td, dict) and td.get("closed"):
        return True
    for entry in data.get("results") or []:
        td = entry.get("todoist") if isinstance(entry, dict) else None
        if isinstance(td, dict) and td.get("closed"):
            return True
    return False


def hide_card() -> bool:
    """Append the dtd task id to the hide file. True if a hide was recorded."""
    t = _target()
    if t is None:
        return False
    tid, path = t
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(tid + "\n")
    except OSError:
        return False
    return True


def unhide_card() -> bool:
    """Strip the dtd task id from the hide file (atomic rewrite). True if
    the file was rewritten."""
    t = _target()
    if t is None:
        return False
    tid, path = t
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    keep = [l for l in lines if l.strip() != tid]
    if len(keep) == len(lines):
        return False
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text("".join(l + "\n" for l in keep), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return False
    return True
