#!/usr/bin/env python3
"""
Format vault markdown files according to spacing rules:
- No blank lines after headers
- No blank lines between list items
- Max 2 consecutive blank lines
- Keep blank line before headers
"""

import os
import re
from pathlib import Path

VAULT_PATH = Path.home() / "vault"

# Directories to skip
SKIP_DIRS = {
    ".git",
    ".obsidian",
    "node_modules",
    "hcmc/readwise"  # Auto-synced, don't edit
}

def should_skip_path(path):
    """Check if path should be skipped."""
    parts = Path(path).parts
    return any(skip in parts for skip in SKIP_DIRS)

def format_spacing(content):
    """Apply spacing rules to markdown content."""
    lines = content.split('\n')
    result = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Track consecutive blank lines
        if line.strip() == '':
            blank_count = 1
            j = i + 1
            while j < len(lines) and lines[j].strip() == '':
                blank_count += 1
                j += 1

            # Reduce 3+ blanks to 2
            if blank_count >= 3:
                result.extend([''] * 2)
                i = j
                continue
            else:
                result.append(line)
                i += 1
                continue

        # Check if this is a header
        is_header = line.strip().startswith('#') and not line.strip().startswith('#[[')

        # Check if next line is blank and line after that exists
        if is_header and i + 1 < len(lines):
            next_line = lines[i + 1]
            # Remove blank line after header
            if next_line.strip() == '' and i + 2 < len(lines):
                result.append(line)
                i += 2  # Skip the blank line
                continue

        # Check if this is a list item or wiki-link
        is_list_item = (
            line.strip().startswith('-') or
            line.strip().startswith('*') or
            line.strip().startswith('[[') or
            (len(line.strip()) > 0 and line.strip()[0].isdigit() and '.' in line.strip()[:5])
        )

        # Check if next line is also a list item with blank in between
        if is_list_item and i + 2 < len(lines):
            next_line = lines[i + 1]
            line_after = lines[i + 2]

            next_is_list = (
                line_after.strip().startswith('-') or
                line_after.strip().startswith('*') or
                line_after.strip().startswith('[[') or
                (len(line_after.strip()) > 0 and line_after.strip()[0].isdigit() and '.' in line_after.strip()[:5])
            )

            # Remove blank line between list items
            if next_line.strip() == '' and next_is_list:
                result.append(line)
                i += 2  # Skip the blank line
                continue

        result.append(line)
        i += 1

    return '\n'.join(result)

def process_file(filepath):
    """Process a single markdown file."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            original = f.read()

        formatted = format_spacing(original)

        if formatted != original:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(formatted)
            return True
        return False

    except Exception as e:
        print(f"Error processing {filepath}: {e}")
        return False

def main():
    """Process all markdown files in vault."""
    print(f"Formatting vault markdown files at {VAULT_PATH}")
    print(f"Skipping: {', '.join(SKIP_DIRS)}\n")

    total_files = 0
    modified_files = 0

    for root, dirs, files in os.walk(VAULT_PATH):
        # Remove skip dirs from dirs list to prevent walking into them
        dirs[:] = [d for d in dirs if not should_skip_path(os.path.join(root, d))]

        if should_skip_path(root):
            continue

        for filename in files:
            if filename.endswith('.md'):
                filepath = os.path.join(root, filename)
                total_files += 1

                if process_file(filepath):
                    modified_files += 1
                    rel_path = os.path.relpath(filepath, VAULT_PATH)
                    print(f"✓ {rel_path}")

    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  Total files processed: {total_files}")
    print(f"  Files modified: {modified_files}")
    print(f"  Files unchanged: {total_files - modified_files}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
