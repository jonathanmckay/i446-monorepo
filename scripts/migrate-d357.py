#!/usr/bin/env python3
"""
migrate-d357.py — One-shot migration of legacy d357 files to the new naming
convention.

Source format: vault/h335/d357/<year>/YYYY-MM-DD <Title with spaces> d357.md
Target format: vault/d357/YYYY-MM-DD-<kebab-slug>.md

Uses `git mv` to preserve history. Doesn't modify file contents.

Usage:
  python3 migrate-d357.py [--dry-run]
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

VAULT = Path.home() / "vault"
LEGACY = VAULT / "h335" / "d357"
NEW = VAULT / "d357"

DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (.+) d357$")


def slugify(title: str) -> str:
    """kebab-case the title; preserve CJK/Arabic, drop other non-word chars."""
    s = title.lower()
    # Replace whitespace runs with single dash
    s = re.sub(r"\s+", "-", s)
    # Keep latin letters, digits, dash, underscore, and unicode letters (CJK/Arabic/etc.)
    s = "".join(c for c in s if c.isalnum() or c in ("-", "_"))
    # Collapse runs of dashes; strip leading/trailing
    s = re.sub(r"-+", "-", s).strip("-_")
    return s


def plan_renames():
    """Yield (old_path, new_path) for every legacy file."""
    for path in sorted(LEGACY.rglob("*.md")):
        stem = path.stem
        m = DATE_PREFIX_RE.match(stem)
        if not m:
            print(f"SKIP (no date prefix or ' d357' suffix): {path.name}", file=sys.stderr)
            continue
        date_str, title = m.group(1), m.group(2)
        slug = slugify(title)
        if not slug:
            print(f"SKIP (empty slug after slugify): {path.name}", file=sys.stderr)
            continue
        new_name = f"{date_str}-{slug}.md"
        yield path, NEW / new_name


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not LEGACY.exists():
        print(f"Nothing to migrate — {LEGACY} doesn't exist.")
        return
    NEW.mkdir(parents=True, exist_ok=True)

    pairs = list(plan_renames())
    if not pairs:
        print("No legacy files to migrate.")
        return

    # Detect collisions
    new_names = {}
    for old, new in pairs:
        if new in new_names:
            print(f"COLLISION: {new.name} from {old} AND {new_names[new]}", file=sys.stderr)
            sys.exit(1)
        if new.exists():
            print(f"COLLISION (target exists): {new}", file=sys.stderr)
            sys.exit(1)
        new_names[new] = old

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Renaming {len(pairs)} files:\n")
    for old, new in pairs:
        rel_old = old.relative_to(VAULT)
        rel_new = new.relative_to(VAULT)
        print(f"  {rel_old}\n    → {rel_new}")
        if not args.dry_run:
            try:
                subprocess.run(
                    ["git", "-C", str(VAULT), "mv", "--", str(rel_old), str(rel_new)],
                    check=True, capture_output=True, text=True,
                )
            except subprocess.CalledProcessError as e:
                # Fallback to plain mv if git mv fails (e.g., file untracked)
                print(f"  git mv failed ({e.stderr.strip()}), falling back to mv", file=sys.stderr)
                shutil.move(str(old), str(new))

    if not args.dry_run:
        # Remove now-empty year folders + LEGACY
        for sub in sorted(LEGACY.iterdir(), reverse=True) if LEGACY.exists() else []:
            if sub.is_dir() and not any(sub.iterdir()):
                sub.rmdir()
                print(f"removed empty dir: {sub.relative_to(VAULT)}")
        if LEGACY.exists() and not any(LEGACY.iterdir()):
            LEGACY.rmdir()
            print(f"removed empty dir: {LEGACY.relative_to(VAULT)}")

    print(f"\n{'Would migrate' if args.dry_run else 'Migrated'} {len(pairs)} files.")


if __name__ == "__main__":
    main()
