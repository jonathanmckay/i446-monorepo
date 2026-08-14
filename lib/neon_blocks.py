"""Shared block-ritual scoring for the -1₦ (build-order) layer.

Single source of truth for: the 地支 (Earthly-Branch) waking blocks, the
emoji→points map (loaded from config/block-rituals.json), parsing the
build-order.md "## -1₲" section, stamping a ritual emoji onto a block header,
and recomputing the day's -1₦ total from the header emoji markers.

Used by:
  - tools/did/did-fast.py          (--ritual: stamp emoji + idempotent P preview)
  - scripts/build-order-daemon.py  (turnover reconcile, authoritative)

Points always land in 0分!P and are written via SET (idempotent), never
appended. This module is pure (stdlib + the JSON config); it does no Excel or
network I/O, so it is unit-testable in isolation. Live re-validation of the
daemon-owned markers (🎯/⏱️/✅) is layered on top by the caller via the
optional `validate` callback to score_day(); when omitted, header stamps are
trusted (the right behavior for the completion-time preview).
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import re
import sys as _sys
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Optional

# Self-locating: guarantees `blocks` resolves regardless of whether the
# caller already put lib/ on sys.path (production) or loaded this file
# directly via importlib (tests) — see lib/blocks.py, the shared
# "has this block started yet?" gate every -1₦ reader must use.
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from blocks import BLOCK_START, is_future_block  # noqa: E402

# 地支 waking blocks, 04:00-anchored, 2h each (shifted −1h from traditional 時辰).
BRANCHES = ["卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]  # 04–06 … 20–22

_CONFIG = Path.home() / "i446-monorepo" / "config" / "block-rituals.json"

NEG1_SECTION = "## -1₲"

BUILD_ORDER = Path.home() / "vault" / "g245" / "5e-1" / "build-order.md"
_BUILD_ORDER_LOCK = BUILD_ORDER.with_suffix(".lock")


@contextmanager
def build_order_lock(build_order: Path | None = None):
    """Exclusive OS-level lock guarding a read-modify-write of build-order.md.
    Locks `build_order`'s own `.lock` sibling (defaults to the real
    BUILD_ORDER path) so tests can pass a tmp_path copy without contending
    with — or depending on — the production lock file.

    Third incident of the same lost-update race as of 2026-08-02 (three
    separate unlocked read-then-write call sites across did-fast.py and
    build-order-daemon.py each independently rediscovered it) — this
    centralizes the lock so every writer shares one primitive instead of
    re-fixing one call site at a time.

    ONLY effective for writers running ON Ix (the file's single canonical
    writer since the 2026-07-27 fix) — flock has no cross-machine reach: a
    caller on Straylight locking Straylight's own `.lock` file provides zero
    protection against Ix's daemon or another Ix-local writer, even though
    Syncthing carries the `.lock` file itself between machines (flock locks
    an open file description on one kernel, not file bytes). Callers that
    might run off-Ix must delegate the read-modify-write to Ix (e.g. over
    ssh) and take this lock THERE — see tools/did/did-fast.py's
    `_stamp_on_ix`/`_run_locked_mutation_on_ix` for the pattern.

    Usage — hold the lock for the ENTIRE read-modify-write, never just the
    write (a lock released between read and write protects nothing):
        with build_order_lock():
            text = BUILD_ORDER.read_text(encoding="utf-8")
            new_text, changed = stamp_emoji(text, block, emoji)
            if changed:
                BUILD_ORDER.write_text(new_text, encoding="utf-8")
    """
    lock_path = (build_order or BUILD_ORDER).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    return json.loads(_CONFIG.read_text(encoding="utf-8"))


def ritual_by_tag() -> dict[str, dict]:
    """tag → ritual dict (emoji, points, mode, …)."""
    return {r["tag"]: r for r in load_config()["rituals"]}


def emoji_points() -> dict[str, int]:
    """emoji → points, from the ritual table (the build-order scoring map)."""
    return {r["emoji"]: r["points"] for r in load_config()["rituals"]}


def ritual_card_tag(name: str, cfg: Optional[dict] = None) -> Optional[str]:
    """Ritual tag for a daemon-created -1neon card name (`😈 <tag>`), else None.

    Completing a ritual card must go through did-fast's run_ritual (header
    emoji stamp + -1₦ credit), but dtd completes tasks BY NAME through the
    generic did-fast path — this resolves such a name back to its ritual tag
    so the caller can reroute it (bug 2026-07-03: -1ibx closed in dtd never
    stamped 📧, and the daemon's header-based reconcile dropped the points).

    The auto_marker is REQUIRED and must be non-empty (fail closed: a task
    literally named `-1g` must never be hijacked). Matching tolerates trailing
    annotations (`😈 -1g (15) [15]`) via whole-token comparison. ALL rituals
    match, auto ones (-1t/-1l) included — the daemon creates cards for the full
    set since 2026-07-05, and a bare `-1t`/`-1l` falling through to the generic
    /did path would mis-route to the unrelated 0₦ habits of the same name.
    run_ritual does NOT branch on mode (2026-07-13 redesign): every tag, auto
    included, gets the header stamp + immediate -1₦ credit. `mode` still
    exists in config for the daemon's own auto-check wiring, and the daemon's
    reconcile audits -1t/-1l's stamp against that check regardless of which
    path wrote it (2026-07-30 correction — see vault/g245/CLAUDE.md).
    """
    if cfg is None:
        cfg = load_config()
    marker = cfg.get("auto_marker", "")
    if not marker or marker not in name:
        return None
    bare = name.replace(marker, "").strip()
    for r in cfg["rituals"]:
        tag = r["tag"]
        if bare == tag or tag in bare.split():
            return tag
    return None


# ---------------------------------------------------------------------------
# Block geometry
# ---------------------------------------------------------------------------

def current_block_index(hour: int) -> int:
    """Index into BRANCHES for the block containing `hour` (clamped 0..8)."""
    return max(0, min(len(BRANCHES) - 1, (hour - 4) // 2))


def current_block(hour: int) -> str:
    return BRANCHES[current_block_index(hour)]


def _block_line_name(line: str) -> str:
    """Block name from a header line like '- 巳 ☀️ 🎯'. '' if not a block header."""
    body = line[2:].strip()  # strip leading "- "
    if not body:
        return ""
    first = body.split()[0]
    return first if first in BRANCHES else ""


def iter_block_lines(text: str):
    """Yield (block_name, line) for each 地支 block header in the -1₲ section."""
    if NEG1_SECTION not in text:
        return
    section = text[text.index(NEG1_SECTION):]
    for line in section.split("\n"):
        if line.startswith("## ") and line != NEG1_SECTION:
            break
        if line.startswith("- ") and not line.startswith("    "):
            name = _block_line_name(line)
            if name:
                yield name, line


# ---------------------------------------------------------------------------
# Stamping & scoring
# ---------------------------------------------------------------------------

def stamp_emoji(text: str, block: str, emoji: str) -> tuple[str, bool]:
    """Append `emoji` to `block`'s header line if not already present.

    Idempotent. Returns (new_text, changed). Only touches header lines inside
    the -1₲ section, so a goal sub-bullet that happens to mention the branch is
    never matched.
    """
    if NEG1_SECTION not in text:
        return text, False
    out: list[str] = []
    changed = False
    in_section = False
    for line in text.split("\n"):
        if line.startswith(NEG1_SECTION):
            in_section = True
        elif line.startswith("## ") and in_section:
            in_section = False
        if (in_section and line.startswith("- ") and not line.startswith("    ")
                and _block_line_name(line) == block and emoji not in line):
            out.append(f"{line.rstrip()} {emoji}")
            changed = True
        else:
            out.append(line)
    return "\n".join(out), changed


def unstamp_emoji(text: str, block: str, emoji: str) -> tuple[str, bool]:
    """Remove `emoji` from `block`'s header line if present. Inverse of
    stamp_emoji, for reversing a ritual completion (undo-fast.py) — only ever
    called when the completion being undone is the one that stamped it
    (caller checks run_ritual's own `stamped` flag), never as a general
    "does this block deserve this marker" edit.

    Idempotent: (text, False) if the emoji wasn't there. Only touches header
    lines inside the -1₲ section, matching stamp_emoji's scope.
    """
    if NEG1_SECTION not in text:
        return text, False
    out: list[str] = []
    changed = False
    in_section = False
    for line in text.split("\n"):
        if line.startswith(NEG1_SECTION):
            in_section = True
        elif line.startswith("## ") and in_section:
            in_section = False
        if (in_section and line.startswith("- ") and not line.startswith("    ")
                and _block_line_name(line) == block and emoji in line):
            new_line = line.replace(f" {emoji}", "", 1)
            if emoji in new_line:  # emoji wasn't space-prefixed (e.g. leads the markers)
                new_line = new_line.replace(emoji, "", 1)
            out.append(" ".join(new_line.split()) if new_line.strip() else new_line.rstrip())
            changed = True
        else:
            out.append(line)
    return "\n".join(out), changed


def flip_goal_checkboxes(text: str, bare_contents: list[str]) -> tuple[str, bool]:
    """Flip `- [ ]` to `- [x]` for each -1₲ goal sub-bullet whose bare text
    (brackets/parens stripped) matches an entry in `bare_contents` — moved
    out of did-fast.py's inline 5e step so it can be lock-protected the same
    way as stamp_emoji (2026-08-02).

    Matches are consumed in order: once a line is flipped for one
    bare_contents entry, it's no longer `[ ]` and can't match a later entry
    in the same call — preserves the original inline loop's one-line-per-
    completion behavior when two completions bare-match the same goal text.
    """
    if NEG1_SECTION not in text:
        return text, False
    lines = text.split("\n")
    changed = False
    for bare in bare_contents:
        if not bare:
            continue
        for i, line in enumerate(lines):
            if not re.match(r"^ {2,4}- \[ \] .+", line):
                continue
            goal = line.strip()[6:]
            bare_goal = re.sub(r"\s*[\[\(\{][^\]\)\}]*[\]\)\}]", "", goal).strip().lower()
            if bare_goal and (bare_goal == bare or bare_goal in bare or bare in bare_goal):
                lines[i] = line.replace("- [ ]", "- [x]", 1)
                changed = True
                break
    return "\n".join(lines), changed


def score_day(
    text: str,
    emoji_pts: Optional[dict[str, int]] = None,
    validate: Optional[Callable[[str, str], bool]] = None,
    now: Optional[dt.datetime] = None,
) -> tuple[list[tuple[str, int]], int, str]:
    """Score every block from its header emojis. Returns (parts, total, formula)
    where parts is [(block, score), …] and formula is the '=0+a+b' SET string
    for 0分!P.

    `validate(block, emoji) -> bool` is an optional per-marker gate: when given,
    a present emoji only scores if validate() returns True (this is how the
    daemon refuses stale 🎯/⏱️/✅). When omitted, header presence is trusted —
    the correct behavior for the completion-time preview, where the stamp was
    just written by the very ritual being completed.

    Blocks that haven't started yet are always excluded via `is_future_block`
    (lib/blocks.py), `now` defaulting to the real current time — the build
    order pre-stamps/auto-awards markers onto blocks ahead of their start, and
    a ritual cannot legitimately be earned in a block that hasn't begun. This
    is the same gate tools/tg/janus.py and skills/1-1n's heatmap already apply
    when reading the header for display; before this fix, score_day was the
    one reader of this file that skipped it, so a future block carrying any
    marker made the live-credited 0分!P total (did-fast.py's immediate
    provisional write, the only caller of score_day) run ahead of what Janus
    showed until the next daemon boundary fire self-healed it back down.
    """
    if emoji_pts is None:
        emoji_pts = emoji_points()
    parts: list[tuple[str, int]] = []
    for block, line in iter_block_lines(text):
        if is_future_block(BLOCK_START.get(block, 0), now):
            continue
        score = 0
        for emoji, pts in emoji_pts.items():
            if emoji in line and (validate is None or validate(block, emoji)):
                score += pts
        if score:
            parts.append((block, score))
    total = sum(s for _, s in parts)
    # No literal leading "0" term: each block contributes exactly one term, so
    # a formula with N blocks scored has N "+"-separated terms, not N+1 (the
    # stray "0" was previously miscounted as a phantom block by anything that
    # reads term-count off this formula).
    formula = "=" + "+".join(str(s) for _, s in parts) if parts else "=0"
    return parts, total, formula
