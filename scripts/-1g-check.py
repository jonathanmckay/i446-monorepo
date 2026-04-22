#!/usr/bin/env python3
"""
-1g-check.py — Report whether the current 2h block has goals set.

Reads the build order, maps wall-clock hour → 地支 block, returns the
block's goals (non-empty bullet content). Designed to be called from /inbound
or /-2n when deciding whether to prompt the user to set goals.

Usage:
  python3 -1g-check.py                    # human-readable
  python3 -1g-check.py --json             # JSON (for scripts)
  python3 -1g-check.py --hour 15          # override current hour (testing)

Exit code: 0 always (unless --strict and status=error).
"""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

MD_FILE = Path.home() / "vault/g245/-1₦ , 0₦ - Neon {Build Order}.md"

# (start_hour, end_hour, 地支, time_range_str)
BLOCKS = [
    (6,  7,  "卯", "06:00-07:59"),
    (8,  9,  "辰", "08:00-09:59"),
    (10, 11, "巳", "10:00-11:59"),
    (12, 13, "午", "12:00-13:59"),
    (14, 15, "未", "14:00-15:59"),
    (16, 17, "申", "16:00-17:59"),
    (18, 19, "酉", "18:00-19:59"),
    (20, 21, "戌", "20:00-21:59"),
    (22, 23, "亥", "22:00-23:59"),
]

SECTION_MARKER = "-1₲"  # "-1₲"


def current_block(hour: int):
    for start, end, branch, time_str in BLOCKS:
        if start <= hour <= end:
            return branch, time_str
    return None, None


def read_block_goals(branch: str, md_path: Path = MD_FILE):
    """Return list of non-empty bullet contents under `- <branch>` inside the ## -1₲ section.

    Returns None if the file or section can't be found.
    """
    if not md_path.exists():
        return None
    lines = md_path.read_text(encoding="utf-8").split("\n")

    section_start = -1
    section_end = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") and SECTION_MARKER in stripped:
            section_start = i
        elif section_start >= 0 and stripped.startswith("## ") and i > section_start:
            section_end = i
            break

    if section_start < 0:
        return None

    items = []
    in_block = False
    block_header = re.compile(r"^- (\S+)\s*$")
    bullet = re.compile(r"^\s+- \[[ xX]\]\s*(.*)$")
    for line in lines[section_start:section_end]:
        m = block_header.match(line)
        if m:
            in_block = (m.group(1) == branch)
            continue
        if in_block:
            m2 = bullet.match(line)
            if m2 and m2.group(1).strip():
                items.append(m2.group(1).strip())
    return items


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.add_argument("--hour", type=int, default=None, help="Override current hour (for testing)")
    p.add_argument("--file", type=Path, default=MD_FILE, help="Override build order path")
    args = p.parse_args()

    hour = args.hour if args.hour is not None else datetime.datetime.now().hour
    branch, time_str = current_block(hour)

    if branch is None:
        result = {"status": "inactive", "hour": hour, "reason": "outside 06-23"}
    else:
        items = read_block_goals(branch, args.file)
        if items is None:
            result = {"status": "error", "reason": "build order or -1₲ section not found"}
        else:
            result = {
                "status": "set" if items else "empty",
                "block": branch,
                "time_range": time_str,
                "hour": hour,
                "goals": items,
                "count": len(items),
            }

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return

    if result["status"] == "inactive":
        print(f"outside active block ({hour}h)")
    elif result["status"] == "error":
        print(f"error: {result['reason']}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"block:  {result['block']} ({result['time_range']})")
        print(f"status: {result['status']} ({result['count']} goal(s))")
        for i, g in enumerate(result["goals"], 1):
            print(f"  {i}. {g}")


if __name__ == "__main__":
    main()
