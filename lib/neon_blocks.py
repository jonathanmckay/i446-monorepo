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
    formula = "=0+" + "+".join(str(s) for _, s in parts) if parts else "=0"
    return parts, total, formula
