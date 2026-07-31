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

import json
from pathlib import Path
from typing import Callable, Optional

# 地支 waking blocks, 04:00-anchored, 2h each (shifted −1h from traditional 時辰).
BRANCHES = ["卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]  # 04–06 … 20–22

_CONFIG = Path.home() / "i446-monorepo" / "config" / "block-rituals.json"

NEG1_SECTION = "## -1₲"


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


def score_day(
    text: str,
    emoji_pts: Optional[dict[str, int]] = None,
    validate: Optional[Callable[[str, str], bool]] = None,
) -> tuple[list[tuple[str, int]], int, str]:
    """Score every block from its header emojis. Returns (parts, total, formula)
    where parts is [(block, score), …] and formula is the '=0+a+b' SET string
    for 0分!P.

    `validate(block, emoji) -> bool` is an optional per-marker gate: when given,
    a present emoji only scores if validate() returns True (this is how the
    daemon refuses stale 🎯/⏱️/✅). When omitted, header presence is trusted —
    the correct behavior for the completion-time preview, where the stamp was
    just written by the very ritual being completed.
    """
    if emoji_pts is None:
        emoji_pts = emoji_points()
    parts: list[tuple[str, int]] = []
    for block, line in iter_block_lines(text):
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
