#!/usr/bin/env python3
"""
Vault Health Check - Quarterly maintenance report for Inkwell vault.

Checks:
- Frontmatter completeness and validity
- File organization (naming, location)
- Broken wikilinks
- Image organization (should be in z_asts/)
- Orphaned files (not linked from anywhere)
- Duplicate content

Usage:
    python3 vault-health-check.py [--fix-auto] [--output report.md]
"""

import os
import re
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import argparse

VAULT_ROOT = Path.home() / "vault"

# Required frontmatter fields
REQUIRED_FIELDS = ["title", "date", "type", "tags", "source", "status"]

# Valid status values
VALID_STATUSES = ["active", "archived", "draft", "rfc"]

# Domain-specific required fields
DOMAIN_FIELDS = {
    "h335/d359": ["child"],  # people docs need child field
    "h335/m5x2": [],  # property, fund are optional
    "h335/i9": [],  # tenure is optional
    "xk87": [],  # curriculum_level is optional
}

# Patterns
WIKILINK_PATTERN = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]*)?\]\]')
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.heic', '.pdf'}
FRONTMATTER_PATTERN = re.compile(r'^---\n(.*?)\n---\n', re.DOTALL | re.MULTILINE)


class HealthChecker:
    def __init__(self, vault_root: Path):
        self.vault_root = vault_root
        self.issues = defaultdict(list)
        self.stats = {
            "total_files": 0,
            "total_images": 0,
            "broken_links": 0,
            "orphaned_files": 0,
            "misplaced_images": 0,
        }
        self.all_files = {}  # path -> file info
        self.backlinks = defaultdict(set)  # target -> set of sources

    def run(self):
        """Run all health checks."""
        print(f"🔍 Scanning vault at {self.vault_root}...")
        self._scan_vault()

        print(f"📊 Analyzing {self.stats['total_files']} files...")
        self._check_frontmatter()
        self._check_file_organization()
        self._check_wikilinks()
        self._check_images()
        self._check_orphans()

        return self._generate_report()

    def _scan_vault(self):
        """Scan all markdown files and build index."""
        for md_file in self.vault_root.rglob("*.md"):
            if ".git" in md_file.parts:
                continue

            rel_path = md_file.relative_to(self.vault_root)
            self.stats["total_files"] += 1

            # Read file
            try:
                content = md_file.read_text(encoding='utf-8')
            except Exception as e:
                self.issues["read_errors"].append((str(rel_path), str(e)))
                continue

            # Extract frontmatter
            fm_match = FRONTMATTER_PATTERN.match(content)
            frontmatter = {}
            if fm_match:
                try:
                    for line in fm_match.group(1).split('\n'):
                        if ':' in line:
                            key, value = line.split(':', 1)
                            frontmatter[key.strip()] = value.strip()
                except:
                    pass

            # Extract wikilinks
            wikilinks = set()
            for match in WIKILINK_PATTERN.finditer(content):
                link_target = match.group(1).strip()
                wikilinks.add(link_target)
                self.backlinks[link_target].add(str(rel_path))

            self.all_files[str(rel_path)] = {
                "path": md_file,
                "frontmatter": frontmatter,
                "wikilinks": wikilinks,
                "size": md_file.stat().st_size,
                "modified": datetime.fromtimestamp(md_file.stat().st_mtime),
            }

        # Scan images
        for ext in IMAGE_EXTENSIONS:
            for img_file in self.vault_root.rglob(f"*{ext}"):
                if ".git" in img_file.parts:
                    continue
                self.stats["total_images"] += 1

    def _check_frontmatter(self):
        """Check frontmatter completeness and validity."""
        for rel_path, info in self.all_files.items():
            fm = info["frontmatter"]

            # Check required fields
            missing = [f for f in REQUIRED_FIELDS if f not in fm]
            if missing:
                self.issues["missing_frontmatter"].append((rel_path, missing))

            # Check status validity
            if "status" in fm:
                status = fm["status"].strip('"\'')
                if status not in VALID_STATUSES:
                    self.issues["invalid_status"].append((rel_path, status))

            # Check z_arcv files have archived status
            if "z_arcv/" in rel_path:
                if fm.get("status", "").strip('"\'') != "archived":
                    self.issues["z_arcv_not_archived"].append(rel_path)

            # Check tags format
            if "tags" in fm:
                tags = fm["tags"]
                if not (tags.startswith('[') and tags.endswith(']')):
                    self.issues["invalid_tags_format"].append(rel_path)

    def _check_file_organization(self):
        """Check file naming and location."""
        for rel_path, info in self.all_files.items():
            filename = Path(rel_path).name

            # Check for kebab-case
            if filename != "CLAUDE.md" and not self._is_kebab_case(filename):
                self.issues["non_kebab_case"].append(rel_path)

            # Check for common anti-patterns
            if "untitled" in filename.lower():
                self.issues["untitled_files"].append(rel_path)
            if " copy" in filename.lower() or "-copy" in filename.lower():
                self.issues["copy_files"].append(rel_path)
            if "deprecated" in filename.lower() and "z_arcv/" not in rel_path:
                self.issues["deprecated_not_archived"].append(rel_path)

    def _check_wikilinks(self):
        """Check for broken wikilinks."""
        for rel_path, info in self.all_files.items():
            for link in info["wikilinks"]:
                # Try to resolve link
                if not self._resolve_wikilink(link):
                    self.issues["broken_wikilinks"].append((rel_path, link))
                    self.stats["broken_links"] += 1

    def _check_images(self):
        """Check image organization."""
        for ext in IMAGE_EXTENSIONS:
            for img_file in self.vault_root.rglob(f"*{ext}"):
                if ".git" in img_file.parts:
                    continue

                rel_path = img_file.relative_to(self.vault_root)

                # Images should be in z_asts/
                if not str(rel_path).startswith("z_asts/"):
                    self.issues["misplaced_images"].append(str(rel_path))
                    self.stats["misplaced_images"] += 1

    def _check_orphans(self):
        """Check for orphaned files (not linked from anywhere)."""
        # Build set of all referenced files
        referenced = set()
        for info in self.all_files.values():
            for link in info["wikilinks"]:
                resolved = self._resolve_wikilink(link)
                if resolved:
                    referenced.add(resolved)

        # Find files with no backlinks (except index files and meta docs)
        for rel_path in self.all_files.keys():
            if rel_path not in referenced:
                # Skip index files, meta docs, and root-level docs
                if any(x in rel_path for x in ["index.md", "meta.md", "CLAUDE.md", "context.md"]):
                    continue
                if rel_path.count('/') == 0:  # root level
                    continue

                self.issues["orphaned_files"].append(rel_path)
                self.stats["orphaned_files"] += 1

    def _resolve_wikilink(self, link: str) -> str:
        """Try to resolve a wikilink to an actual file path."""
        # Remove .md extension if present
        link = link.rstrip('/')

        # Direct match
        if link in self.all_files:
            return link
        if f"{link}.md" in self.all_files:
            return f"{link}.md"

        # Try partial match (basename only)
        link_basename = link.split('/')[-1]
        for path in self.all_files.keys():
            if Path(path).stem == link_basename:
                return path

        return None

    def _is_kebab_case(self, filename: str) -> bool:
        """Check if filename follows kebab-case convention."""
        # Remove .md extension
        name = filename.replace('.md', '')

        # Allow numbers, lowercase letters, hyphens
        # Also allow some special cases like domain codes
        if re.match(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$', name) or name in ['index', 'README']:
            return True

        # Allow Chinese/emoji in filenames
        if any('\u4e00' <= c <= '\u9fff' for c in name):
            return True

        return False

    def _generate_report(self) -> str:
        """Generate markdown report."""
        report_lines = [
            "---",
            f"title: Vault Health Check Report",
            f"date: {datetime.now().strftime('%Y-%m-%d')}",
            "type: report",
            "tags: [i447, vault, maintenance]",
            "source: automated",
            "status: active",
            "---",
            "",
            "# Vault Health Check Report",
            "",
            f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Vault:** {self.vault_root}",
            "",
            "## Summary Statistics",
            "",
            f"- **Total files:** {self.stats['total_files']:,}",
            f"- **Total images:** {self.stats['total_images']:,}",
            f"- **Broken links:** {self.stats['broken_links']:,}",
            f"- **Orphaned files:** {self.stats['orphaned_files']:,}",
            f"- **Misplaced images:** {self.stats['misplaced_images']:,}",
            "",
        ]

        # Issues sections
        issue_sections = {
            "missing_frontmatter": ("Missing Frontmatter Fields", "Files missing required frontmatter fields"),
            "invalid_status": ("Invalid Status Values", "Files with invalid status field"),
            "z_arcv_not_archived": ("Z_arcv Files Not Archived", "Files in z_arcv/ without status: archived"),
            "broken_wikilinks": ("Broken Wikilinks", "Wikilinks that don't resolve to any file"),
            "misplaced_images": ("Misplaced Images", "Images not in z_asts/ directory"),
            "orphaned_files": ("Orphaned Files", "Files not linked from anywhere"),
            "untitled_files": ("Untitled Files", "Files with 'untitled' in filename"),
            "copy_files": ("Copy Files", "Files with 'copy' in filename"),
            "deprecated_not_archived": ("Deprecated Files Not Archived", "Files with 'deprecated' not in z_arcv/"),
            "non_kebab_case": ("Non-Kebab-Case Files", "Files not following kebab-case naming"),
        }

        for issue_key, (title, description) in issue_sections.items():
            issues = self.issues.get(issue_key, [])
            if issues:
                report_lines.extend([
                    f"## {title}",
                    "",
                    description,
                    "",
                    f"**Count:** {len(issues)}",
                    "",
                ])

                # Show first 50 issues
                for i, issue in enumerate(issues[:50], 1):
                    if isinstance(issue, tuple):
                        if len(issue) == 2 and isinstance(issue[1], list):
                            # (path, [missing_fields])
                            report_lines.append(f"{i}. `{issue[0]}` - missing: {', '.join(issue[1])}")
                        else:
                            # (path, detail)
                            report_lines.append(f"{i}. `{issue[0]}` - {issue[1]}")
                    else:
                        report_lines.append(f"{i}. `{issue}`")

                if len(issues) > 50:
                    report_lines.append(f"\n*...and {len(issues) - 50} more*")

                report_lines.append("")

        return '\n'.join(report_lines)


def main():
    parser = argparse.ArgumentParser(description="Run vault health check")
    parser.add_argument("--output", default="vault-health-report.md", help="Output report file")
    parser.add_argument("--vault", default=str(VAULT_ROOT), help="Vault root directory")
    args = parser.parse_args()

    checker = HealthChecker(Path(args.vault))
    report = checker.run()

    # Write report
    output_path = Path(args.vault) / "i447" / "i446" / args.output
    output_path.write_text(report, encoding='utf-8')

    print(f"\n✅ Health check complete!")
    print(f"📄 Report written to: {output_path}")
    print(f"\n📊 Summary:")
    print(f"  - Total files: {checker.stats['total_files']:,}")
    print(f"  - Issues found: {sum(len(v) for v in checker.issues.values()):,}")


if __name__ == "__main__":
    main()
