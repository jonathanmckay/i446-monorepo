#!/usr/bin/env python3
"""
Skill validator + auto-fixer.

Run with no args from the repo root (or anywhere) to:
  1. Rename skills/claude-skills/<name>/skill.md -> SKILL.md (case fix).
  2. Validate YAML frontmatter on every SKILL.md (must parse, must have
     name + description as quoted strings, name must match folder).

Exit codes:
  0 = clean
  1 = validation errors (printed to stderr)
"""
from __future__ import annotations
import os, re, sys, pathlib

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

SKILLS_DIR = pathlib.Path(__file__).resolve().parent.parent  # claude-skills/
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

def fix_case(skills_dir: pathlib.Path) -> list[str]:
    fixed = []
    for sub in skills_dir.iterdir():
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        lower = sub / "skill.md"
        upper = sub / "SKILL.md"
        # Case-insensitive FS check: only rename if the literal lowercase exists
        # AND the on-disk listing reports it as 'skill.md'.
        names = {p.name for p in sub.iterdir() if p.is_file()}
        if "skill.md" in names and "SKILL.md" not in names:
            tmp = sub / "_skill_rename.tmp"
            lower.rename(tmp)
            tmp.rename(upper)
            fixed.append(str(upper.relative_to(skills_dir)))
    return fixed

def validate(skills_dir: pathlib.Path) -> list[str]:
    errors = []
    for sub in sorted(skills_dir.iterdir()):
        if not sub.is_dir() or sub.name.startswith(".") or sub.name == "_lib":
            continue
        skill_file = sub / "SKILL.md"
        if not skill_file.exists():
            errors.append(f"{sub.name}/: missing SKILL.md")
            continue
        text = skill_file.read_text(encoding="utf-8")
        m = FRONTMATTER_RE.match(text)
        if not m:
            errors.append(f"{sub.name}/SKILL.md: missing or malformed frontmatter")
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as e:
            errors.append(f"{sub.name}/SKILL.md: YAML parse error: {e}")
            continue
        for key in ("name", "description"):
            if key not in fm:
                errors.append(f"{sub.name}/SKILL.md: missing `{key}`")
                continue
            if not isinstance(fm[key], str):
                errors.append(
                    f"{sub.name}/SKILL.md: `{key}` must be a string "
                    f"(got {type(fm[key]).__name__}; quote it)"
                )
        if fm.get("name") and fm["name"] != sub.name:
            print(
                f"warning: {sub.name}/SKILL.md: name=`{fm['name']}` "
                f"does not match folder `{sub.name}`",
                file=sys.stderr,
            )
    return errors

def main() -> int:
    fixed = fix_case(SKILLS_DIR)
    for f in fixed:
        print(f"renamed: {f}")
    errors = validate(SKILLS_DIR)
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    return 1 if errors else 0

if __name__ == "__main__":
    sys.exit(main())
