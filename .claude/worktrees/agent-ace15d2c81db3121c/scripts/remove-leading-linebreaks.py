#!/usr/bin/env python3
"""
Remove leading linebreaks from markdown files in the vault.
- Removes blank lines at the very start of files
- Removes excess blank lines after frontmatter (keeps max 2)

Usage:
    python3 remove-leading-linebreaks.py [--dry-run]
"""

import os
import re
import argparse
from pathlib import Path

VAULT_ROOT = Path.home() / "vault"
FRONTMATTER_PATTERN = re.compile(r'^---\n(.*?)\n---\n', re.DOTALL | re.MULTILINE)


def remove_leading_linebreaks(file_path: Path, dry_run: bool = False) -> bool:
    """
    Remove leading blank lines from a markdown file.
    - Strip blank lines at the start of the file
    - Remove excess blank lines after frontmatter (max 2 allowed)

    Returns True if file was modified, False otherwise.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return False

    original_content = content
    modified = False

    # First, strip any leading newlines from the entire file
    if content.startswith('\n'):
        content = content.lstrip('\n')
        modified = True

    # Check if file has frontmatter
    fm_match = FRONTMATTER_PATTERN.match(content)

    if fm_match:
        # Get frontmatter and the rest
        frontmatter_end = fm_match.end()
        frontmatter = content[:frontmatter_end]
        rest_of_file = content[frontmatter_end:]

        # Remove leading blank lines after frontmatter, but allow up to 2
        original_rest = rest_of_file
        rest_of_file = rest_of_file.lstrip('\n')

        # Add back exactly 2 newlines (one blank line after ---)
        if rest_of_file:  # Only if there's content after frontmatter
            rest_of_file = '\n\n' + rest_of_file

        if original_rest != rest_of_file:
            content = frontmatter + rest_of_file
            modified = True

    if not modified:
        return False

    if dry_run:
        rel_path = file_path.relative_to(VAULT_ROOT)
        print(f"Would clean leading linebreaks: {rel_path}")
        return True

    # Write back
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    except Exception as e:
        print(f"Error writing {file_path}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Remove leading linebreaks from markdown files")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed without modifying files")
    args = parser.parse_args()

    print(f"Scanning vault at {VAULT_ROOT}...")

    md_files = list(VAULT_ROOT.rglob("*.md"))
    print(f"Found {len(md_files)} markdown files")

    modified_count = 0

    for md_file in md_files:
        # Skip .git directory
        if ".git" in md_file.parts:
            continue

        if remove_leading_linebreaks(md_file, dry_run=args.dry_run):
            modified_count += 1

    if args.dry_run:
        print(f"\n✅ Dry run complete!")
        print(f"Would modify {modified_count} files")
    else:
        print(f"\n✅ Complete!")
        print(f"Modified {modified_count} files")


if __name__ == "__main__":
    main()
