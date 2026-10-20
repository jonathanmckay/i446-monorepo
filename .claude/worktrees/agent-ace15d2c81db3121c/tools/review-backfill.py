#!/usr/bin/env python3
"""Backfill inter-review links (related, series_prev, series_next) across all vault reviews.

Usage:
  python3 review-backfill.py          # dry run (shows what would change)
  python3 review-backfill.py --apply  # write changes
"""

import re
import sys
from pathlib import Path
from collections import defaultdict

REVIEWS_DIR = Path.home() / "vault/hcmc/reviews"
DRY_RUN = "--apply" not in sys.argv


def parse_frontmatter(text):
    """Return (frontmatter_str, body) or (None, text)."""
    m = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
    if not m:
        return None, text
    return m.group(1), m.group(2)


def get_field(fm, field):
    """Get a scalar field value from frontmatter string."""
    m = re.search(rf'^{field}:\s*"?(.+?)"?\s*$', fm, re.MULTILINE)
    if m:
        val = m.group(1).strip().strip('"').strip("'")
        return val
    return None


def get_list_field(fm, field):
    """Get a list field (YAML block style) from frontmatter."""
    pattern = rf'^{field}:\s*\n((?:  - .+\n)*)'
    m = re.search(pattern, fm, re.MULTILINE)
    if not m:
        return []
    items = re.findall(r'  - "?(.+?)"?\s*$', m.group(1), re.MULTILINE)
    return items


def set_list_field(fm, field, values):
    """Set or replace a list field in frontmatter. Returns updated fm."""
    if not values:
        return fm
    block = f"{field}:\n" + "".join(f'  - "{v}"\n' for v in sorted(values))
    # Replace existing
    pattern = rf'^{field}:\s*\n(?:  - .+\n)*'
    if re.search(pattern, fm, re.MULTILINE):
        fm = re.sub(pattern, block, fm, flags=re.MULTILINE)
    else:
        fm = fm.rstrip("\n") + "\n" + block
    return fm


def set_scalar_field(fm, field, value):
    """Set or replace a scalar field. Returns updated fm."""
    if value is None:
        return fm
    line = f'{field}: "{value}"'
    pattern = rf'^{field}:.*$'
    if re.search(pattern, fm, re.MULTILINE):
        fm = re.sub(pattern, line, fm, flags=re.MULTILINE)
    else:
        fm = fm.rstrip("\n") + "\n" + line + "\n"
    return fm


def main():
    # Phase 1: scan all reviews
    reviews = {}  # relpath -> {author, series, series_number, fm, body, path}
    authors = defaultdict(list)  # author -> [relpath, ...]
    series_map = defaultdict(list)  # series -> [(relpath, series_number), ...]

    for p in sorted(REVIEWS_DIR.rglob("*.md")):
        rel = str(p.relative_to(REVIEWS_DIR))
        # skip index files (year summaries, etc.)
        if "/" not in rel:
            continue
        text = p.read_text()
        fm, body = parse_frontmatter(text)
        if fm is None:
            continue

        author = get_field(fm, "author")
        if not author:
            continue

        series = get_field(fm, "series")
        sn_m = re.search(r"^series_number:\s*(\d+)", fm, re.MULTILINE)
        sn = int(sn_m.group(1)) if sn_m else None

        reviews[rel] = {
            "author": author,
            "series": series,
            "series_number": sn,
            "fm": fm,
            "body": body,
            "path": p,
        }
        authors[author].append(rel)
        if series:
            series_map[series].append((rel, sn))

    # Phase 2: compute links
    changes = {}  # relpath -> new_fm

    for rel, info in reviews.items():
        fm = info["fm"]
        changed = False

        # Related: all other reviews by same author
        same_author = [r for r in authors[info["author"]] if r != rel]
        if same_author:
            existing = get_list_field(fm, "related")
            merged = sorted(set(existing) | set(same_author))
            if merged != sorted(existing):
                fm = set_list_field(fm, "related", merged)
                changed = True

        # Series prev/next
        if info["series"]:
            siblings = series_map[info["series"]]
            # Only link if we have series_numbers to order by
            numbered = [(r, sn) for r, sn in siblings if sn is not None]
            if numbered and info["series_number"] is not None:
                numbered.sort(key=lambda x: x[1])
                my_idx = None
                for i, (r, sn) in enumerate(numbered):
                    if r == rel:
                        my_idx = i
                        break
                if my_idx is not None:
                    # prev
                    if my_idx > 0:
                        prev_rel = numbered[my_idx - 1][0]
                        existing_prev = get_field(fm, "series_prev")
                        if existing_prev != prev_rel:
                            fm = set_scalar_field(fm, "series_prev", prev_rel)
                            changed = True
                    # next
                    if my_idx < len(numbered) - 1:
                        next_rel = numbered[my_idx + 1][0]
                        existing_next = get_field(fm, "series_next")
                        if existing_next != next_rel:
                            fm = set_scalar_field(fm, "series_next", next_rel)
                            changed = True

        if changed:
            changes[rel] = fm

    # Phase 3: report or apply
    print(f"Total reviews scanned: {len(reviews)}")
    print(f"Authors with 2+ reviews: {sum(1 for a, r in authors.items() if len(r) > 1)}")
    print(f"Series: {len(series_map)}")
    print(f"Reviews to update: {len(changes)}")
    print()

    if DRY_RUN:
        # Show sample changes
        shown = 0
        for rel, new_fm in sorted(changes.items()):
            if shown >= 10:
                print(f"... and {len(changes) - shown} more")
                break
            old_fm = reviews[rel]["fm"]
            # Show just the diff in related/series fields
            old_related = get_list_field(old_fm, "related")
            new_related = get_list_field(new_fm, "related")
            added = set(new_related) - set(old_related)

            old_prev = get_field(old_fm, "series_prev")
            new_prev = get_field(new_fm, "series_prev")
            old_next = get_field(old_fm, "series_next")
            new_next = get_field(new_fm, "series_next")

            parts = []
            if added:
                parts.append(f"+{len(added)} related")
            if new_prev and new_prev != old_prev:
                parts.append(f"series_prev={new_prev}")
            if new_next and new_next != old_next:
                parts.append(f"series_next={new_next}")

            print(f"  {rel}: {', '.join(parts)}")
            shown += 1
        print()
        print("Run with --apply to write changes.")
    else:
        for rel, new_fm in changes.items():
            info = reviews[rel]
            new_text = f"---\n{new_fm}---\n{info['body']}"
            info["path"].write_text(new_text)
        print(f"Updated {len(changes)} files.")


if __name__ == "__main__":
    main()
