"""Earthly Branch (地支) 2h block math + build-order parsing.

Used by /-1g, /-2n, /inbound, /did Step 5b, /0g, /0r.
Replaces six near-duplicate regex implementations across skills.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

VAULT = Path.home() / "vault"
BUILD_ORDER = VAULT / "g245" / "-1₦ , 0₦ - Neon {Build Order}.md"

# (start_hour, branch). The 2h block is [start, start+2). Hours 0-3 land in 亥 (sleep).
BLOCKS = [
    (4, "寅"), (6, "卯"), (8, "辰"), (10, "巳"), (12, "午"),
    (14, "未"), (16, "申"), (18, "酉"), (20, "戌"), (22, "亥"),
]


def current_block(now: datetime | None = None) -> tuple[int, str]:
    """Return (start_hour, branch) for the block covering `now` (default: now)."""
    h = (now or datetime.now()).hour
    for start, label in reversed(BLOCKS):
        if h >= start:
            return start, label
    return 22, "亥"  # 0-3 wrap into prior day's 亥


def block_label(now: datetime | None = None) -> str:
    start, label = current_block(now)
    return f"{label} ({start:02d}:00–{start + 1:02d}:59)"


_SECTION_RE = re.compile(r"^##\s+-1₲\s*$", re.MULTILINE)
_NEXT_HEADER_RE = re.compile(r"^##\s+", re.MULTILINE)
_NEXT_BRANCH_RE = re.compile(r"^-\s+[一-鿿](?:\s|$)", re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^\s*-\s*\[\s*[ xX]?\s*\]\s*(.+)$")


def parse_block_goals(branch: str, path: Path = BUILD_ORDER) -> list[str]:
    """Return non-empty `- [ ]` items under the given 地支 in the -1₲ section."""
    if not branch or not path.exists():
        return []
    text = path.read_text()
    m = _SECTION_RE.search(text)
    if not m:
        return []
    body = text[m.end():]
    end = _NEXT_HEADER_RE.search(body)
    if end:
        body = body[:end.start()]
    branch_hit = re.search(rf"^-\s+{re.escape(branch)}(?:\s|$)", body, re.MULTILINE)
    if not branch_hit:
        return []
    rest = body[branch_hit.end():]
    nxt = _NEXT_BRANCH_RE.search(rest)
    block_text = rest[:nxt.start()] if nxt else rest
    out: list[str] = []
    for line in block_text.splitlines():
        cm = _CHECKBOX_RE.match(line)
        if cm:
            goal = cm.group(1).strip()
            if goal:
                out.append(goal)
    return out


_ANNOT_RE = re.compile(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]\s*$")


def _bare(s: str) -> str:
    """Strip trailing (N)/[N]/{N} annotations for fuzzy matching."""
    while True:
        new = _ANNOT_RE.sub("", s).strip()
        if new == s:
            return s
        s = new


def flip_checkbox(task_content: str, path: Path = BUILD_ORDER) -> bool:
    """Flip `- [ ]` → `- [x]` on the line matching `task_content`.

    Tries exact match first, then bare-text fallback. Returns True if a flip
    happened, False if no match (skip silently per /did Step 5b).
    """
    if not path.exists():
        return False
    text = path.read_text()
    target = task_content.strip()
    bare_target = _bare(target)
    new_lines: list[str] = []
    flipped = False
    for line in text.splitlines():
        if not flipped and "- [ ]" in line:
            content = line.split("- [ ]", 1)[1].strip()
            if content == target or _bare(content) == bare_target:
                line = line.replace("- [ ]", "- [x]", 1)
                flipped = True
        new_lines.append(line)
    if flipped:
        path.write_text("\n".join(new_lines) + ("\n" if text.endswith("\n") else ""))
    return flipped
