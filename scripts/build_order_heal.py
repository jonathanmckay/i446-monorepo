"""Heal Syncthing-conflict losses in the build order's `## -1₲` section.

Straylight stamps a ritual (writes the 🎯 goal marker + the goal's body text)
at completion time, while the Ix daemon rewrites the same file on its own
2-hour cadence (Time-entry appends, ⏱️/✅ auto-markers). When both sides
touch the file close together, Syncthing's conflict resolution keeps only
one side and demotes the other to a local `build-order.sync-conflict-*.md`
copy that nothing else ever reads — so a goal set on Straylight can vanish
from the live file it never even conflicted with visibly.

This matters because `_block_has_goals` (build-order-daemon.py) requires
real goal text under a block to keep that block's 🎯 credit; a body-text
loss like this permanently costs those 3 points on every future reconcile
of that block, even though the goal really was set (2026-07-29: 辰 lost its
🎯 this way, scoring 10/13 instead of 13/13).

`heal()` scans for `.sync-conflict-*` siblings of the build order file and,
for any block whose live copy is missing goal text or a daemon marker that
a conflict copy has, merges it back in. Union-merge only: never removes or
overwrites anything already present in the live copy, so a later validation
pass is still free to strip whatever isn't actually earned.
"""
from __future__ import annotations

import re
from pathlib import Path

NEG1_SECTION = "## -1₲"
GOAL_LINE_RE = re.compile(r"^    - \[[ xX]\]\s*\S")
BLANK_GOAL_LINE_RE = re.compile(r"^    - \[ \]\s*$")
DAEMON_MARKERS = ("🎯", "⏱️", "✅")
FRONTMATTER_DATE_RE = re.compile(r"^date:\s*(\S+)\s*$", re.MULTILINE)


def _frontmatter_date(text: str) -> str | None:
    """The `date:` value from the file's YAML frontmatter, or None if absent.
    Block names (辰/午/申/...) recur every day, so a conflict copy must be
    checked against the SAME day as the live file before merging anything —
    otherwise a stale conflict from a different day corrupts today's file
    under the guise of "the same block" (2026-07-29 incident: healing pulled
    昨天/last-week goal text into today's 辰/酉/戌/亥 blocks)."""
    m = FRONTMATTER_DATE_RE.search(text)
    return m.group(1) if m else None


def _block_line_name(line: str) -> str:
    """Block name from a `- 卯 ...` header line: the first whitespace token
    after the bullet (mirrors build-order-daemon.py's `_block_line_name`)."""
    rest = line[2:].strip()
    return rest.split()[0] if rest else ""


def _parse_neg1_blocks(text: str) -> dict[str, list[str]]:
    """block_name -> its raw lines (header line first, then body) within the
    `## -1₲` section. Empty dict if the section is missing."""
    if NEG1_SECTION not in text:
        return {}
    section = text[text.index(NEG1_SECTION):]
    blocks: dict[str, list[str]] = {}
    current = None
    for line in section.split("\n")[1:]:
        if line.startswith("## "):
            break
        if line.startswith("- ") and not line.startswith("    "):
            current = _block_line_name(line)
            blocks.setdefault(current, []).append(line)
        elif current is not None:
            blocks[current].append(line)
    return blocks


def _goal_lines(block_lines: list[str]) -> list[str]:
    return [l for l in block_lines if GOAL_LINE_RE.match(l)]


def _conflict_files(build_order: Path) -> list[Path]:
    pattern = f"{build_order.stem}.sync-conflict-*{build_order.suffix}"
    return sorted(build_order.parent.glob(pattern))


def _merge_markers(text: str, block_name: str, markers: list[str]) -> str:
    """Append any of `markers` not already on `block_name`'s header line."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("- ") and not line.startswith("    ") and _block_line_name(line) == block_name:
            missing = [m for m in markers if m not in line]
            if missing:
                lines[i] = line.rstrip() + " " + " ".join(missing)
            return "\n".join(lines)
    return text


def _merge_goal_lines(text: str, block_name: str, goal_lines: list[str]) -> str:
    """Replace the contiguous run of blank `- [ ]` placeholder lines directly
    under `block_name`'s header with `goal_lines` recovered from a conflict
    copy. No-op if the header isn't found or has no blank placeholder run."""
    lines = text.split("\n")
    in_section = False
    for i, line in enumerate(lines):
        if line.strip() == NEG1_SECTION:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        if line.startswith("- ") and not line.startswith("    ") and _block_line_name(line) == block_name:
            j = i + 1
            while j < len(lines) and BLANK_GOAL_LINE_RE.match(lines[j]):
                j += 1
            if j == i + 1:
                return text  # no blank placeholder run to replace
            lines[i + 1:j] = goal_lines
            return "\n".join(lines)
    return text


def heal(build_order: Path) -> dict:
    """Union-merge missing goal text/markers from `.sync-conflict-*` copies
    into the live build order. Returns
    `{"merged": [{"file": str, "added": [glyphs/markers recovered]}]}`."""
    result: dict = {"merged": []}
    if not build_order.exists():
        return result

    live_text = build_order.read_text(encoding="utf-8")
    conflicts = _conflict_files(build_order)
    if not conflicts:
        return result

    live_date = _frontmatter_date(live_text)

    for conflict in conflicts:
        try:
            conflict_text = conflict.read_text(encoding="utf-8")
        except OSError:
            continue

        conflict_date = _frontmatter_date(conflict_text)
        if live_date is None or conflict_date is None or conflict_date != live_date:
            continue  # different day (or undated) -- never merge across days

        live_blocks = _parse_neg1_blocks(live_text)
        conflict_blocks = _parse_neg1_blocks(conflict_text)
        added: list[str] = []

        for block_name, conflict_lines in conflict_blocks.items():
            live_lines = live_blocks.get(block_name)
            if live_lines is None:
                continue  # block not present in the live file today; skip

            if not _goal_lines(live_lines):
                conflict_goals = _goal_lines(conflict_lines)
                if conflict_goals:
                    live_text = _merge_goal_lines(live_text, block_name, conflict_goals)
                    live_blocks = _parse_neg1_blocks(live_text)
                    added.append(f"{block_name}:goal-text")

            live_header = live_blocks.get(block_name, [""])[0]
            conflict_header = conflict_lines[0] if conflict_lines else ""
            missing_markers = [m for m in DAEMON_MARKERS
                              if m in conflict_header and m not in live_header]
            if missing_markers:
                live_text = _merge_markers(live_text, block_name, missing_markers)
                live_blocks = _parse_neg1_blocks(live_text)
                added.extend(missing_markers)

        if added:
            result["merged"].append({"file": str(conflict), "added": added})

    if result["merged"]:
        build_order.write_text(live_text, encoding="utf-8")
    return result
