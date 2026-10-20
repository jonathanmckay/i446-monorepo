#!/usr/bin/env python3
"""
Audit every SKILL.md under ~/.claude/skills/ for broken script/file references.

Catches the class of bugs where a skill's prose says "run X.py" but X.py was
never written (e.g. /ate's neon-ate.py). Run nightly via cron, or before
shipping a new skill.

Heuristic: extract every `~/...`, `/Users/...`, and `python3 <path>` mention
in prose, then stat() it. Flags missing paths only.
"""

import re
import sys
from pathlib import Path

SKILL_DIRS = [
    Path.home() / ".claude/skills",
    Path.home() / "i446-monorepo/skills",
]

# Match an absolute path, a tilde path, or `python3 <path>` invocations.
PATH_RE = re.compile(
    r"(?:python3?\s+|bash\s+)?(~/[\w./-]+\.\w+|/Users/[\w./-]+\.\w+)"
)


def candidates(text: str) -> set[Path]:
    out: set[Path] = set()
    for m in PATH_RE.finditer(text):
        raw = m.group(1)
        p = Path(raw).expanduser()
        # Skip vault paths — those are docs, not executables
        if "/vault/" in str(p):
            continue
        # Skip placeholder examples like X.py, foo.py
        if p.stem in {"X", "x", "foo", "bar", "<X>", "<file>", "<path>"}:
            continue
        out.add(p)
    return out


def audit(root: Path) -> list[tuple[Path, Path]]:
    """Return list of (skill_md, missing_path) tuples."""
    broken: list[tuple[Path, Path]] = []
    for md in root.rglob("SKILL.md"):
        text = md.read_text(errors="ignore")
        for c in candidates(text):
            if not c.exists():
                broken.append((md, c))
    return broken


def main():
    total = 0
    for root in SKILL_DIRS:
        if not root.exists():
            continue
        broken = audit(root)
        if not broken:
            continue
        print(f"\n# {root}\n")
        # Group by skill
        by_skill: dict[Path, list[Path]] = {}
        for skill, missing in broken:
            by_skill.setdefault(skill, []).append(missing)
        for skill in sorted(by_skill):
            rel = skill.relative_to(root)
            print(f"## {rel.parent}")
            for m in sorted(set(by_skill[skill])):
                print(f"  ✗ {m}")
                total += 1
    if total == 0:
        print("All referenced paths exist. ✓")
        return 0
    print(f"\n{total} broken reference(s). Either create the file or remove the mention from SKILL.md.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
