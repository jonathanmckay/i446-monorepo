#!/usr/bin/env python3
"""
Fix markdown files in ~/vault/ for two issues:
1. Remove blank lines between closing frontmatter --- and first content line.
2. Remove duplicate title headings that match the frontmatter title field.
"""

import os
import re
from pathlib import Path

VAULT = Path.home() / "vault"

# Directories to skip
SKIP_DIRS = {".git", "node_modules"}
# Paths to skip (relative to vault)
SKIP_PREFIXES = ["hcmc/readwise/"]

# Stats
stats = {
    "files_scanned": 0,
    "files_modified": 0,
    "whitespace_fixed": 0,
    "duplicate_titles_removed": 0,
}
modified_files = []


def should_skip(filepath):
    """Check if a file should be skipped."""
    rel = filepath.relative_to(VAULT)
    # Skip files in .git or node_modules anywhere in the path
    for part in rel.parts:
        if part in SKIP_DIRS:
            return True
    # Skip hcmc/readwise/
    rel_str = str(rel)
    for prefix in SKIP_PREFIXES:
        if rel_str.startswith(prefix):
            return True
    return False


def normalize_title(title):
    """Normalize a title for comparison: strip quotes, whitespace, lowercase."""
    title = title.strip()
    # Remove surrounding quotes (single or double)
    if (title.startswith('"') and title.endswith('"')) or \
       (title.startswith("'") and title.endswith("'")):
        title = title[1:-1]
    return title.strip().lower()


def extract_frontmatter_title(frontmatter_lines):
    """Extract the title from frontmatter lines."""
    for line in frontmatter_lines:
        # Match title: "value" or title: value
        m = re.match(r'^title\s*:\s*(.+)$', line, re.IGNORECASE)
        if m:
            return normalize_title(m.group(1))
    return None


def process_file(filepath):
    """Process a single markdown file. Returns True if modified."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return False

    lines = content.split("\n")

    # Check for frontmatter: first line must be ---
    if not lines or lines[0].strip() != "---":
        return False

    # Find closing ---
    closing_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing_idx = i
            break

    if closing_idx is None:
        return False

    frontmatter_lines = lines[1:closing_idx]
    after_frontmatter = lines[closing_idx + 1:]

    modified = False
    whitespace_fixed = False
    duplicate_removed = False

    # --- Fix 1: Remove blank lines between closing --- and first content ---
    # Count leading blank lines in after_frontmatter
    blank_count = 0
    for line in after_frontmatter:
        if line.strip() == "":
            blank_count += 1
        else:
            break

    if blank_count > 0:
        after_frontmatter = after_frontmatter[blank_count:]
        modified = True
        whitespace_fixed = True

    # --- Fix 2: Remove duplicate title heading ---
    fm_title = extract_frontmatter_title(frontmatter_lines)
    if fm_title and after_frontmatter:
        first_line = after_frontmatter[0]
        heading_match = re.match(r'^#\s+(.+)$', first_line)
        if heading_match:
            heading_text = normalize_title(heading_match.group(1))
            if heading_text == fm_title:
                # Remove the heading line
                after_frontmatter = after_frontmatter[1:]
                modified = True
                duplicate_removed = True

                # If the next line(s) are blank, keep at most one blank line
                # before content (but if there's no content, just leave empty)
                if after_frontmatter:
                    blank_after = 0
                    for line in after_frontmatter:
                        if line.strip() == "":
                            blank_after += 1
                        else:
                            break

                    if blank_after > 1:
                        # Keep just one blank line
                        after_frontmatter = [""] + after_frontmatter[blank_after:]
                    # If blank_after == 0 or 1, leave as-is

    if not modified:
        return False

    # Reconstruct file
    new_lines = lines[:closing_idx + 1] + after_frontmatter
    new_content = "\n".join(new_lines)

    filepath.write_text(new_content, encoding="utf-8")

    if whitespace_fixed:
        stats["whitespace_fixed"] += 1
    if duplicate_removed:
        stats["duplicate_titles_removed"] += 1
    stats["files_modified"] += 1
    modified_files.append(str(filepath.relative_to(VAULT)))

    return True


def main():
    # Walk vault directory
    for root, dirs, files in os.walk(VAULT):
        # Prune skipped directories
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for fname in sorted(files):
            if not fname.endswith(".md"):
                continue

            filepath = Path(root) / fname
            if should_skip(filepath):
                continue

            stats["files_scanned"] += 1
            process_file(filepath)

    # Print results
    print("=" * 60)
    print("Vault Markdown Fix - Summary")
    print("=" * 60)
    print(f"Files scanned:              {stats['files_scanned']}")
    print(f"Files modified:             {stats['files_modified']}")
    print(f"  Whitespace fixed:         {stats['whitespace_fixed']}")
    print(f"  Duplicate titles removed: {stats['duplicate_titles_removed']}")
    print()

    if modified_files:
        show = min(10, len(modified_files))
        print(f"First {show} modified files:")
        for f in modified_files[:10]:
            print(f"  - {f}")
    else:
        print("No files needed modification.")


if __name__ == "__main__":
    main()
