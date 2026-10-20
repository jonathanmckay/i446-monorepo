#!/usr/bin/env python3
from __future__ import annotations
"""
Vault Style Auditor — checks vault markdown files against Inkwell style rules.

Rules checked:
  1. No double title (frontmatter title + H1 heading)
  2. Dates in filenames/headings use YYYY.MM.DD (not hyphens)
  3. Frontmatter date fields use ISO YYYY-MM-DD
  4. TODOs (- [ ]) appear before main content, not buried
  5. d359 files: profile section at top, meeting notes below
  6. No em-dashes (—) in content
  7. Folder note convention (index matches parent folder name)
  8. kebab-case filenames (except CLAUDE.md, CONTEXT.md, STYLE.md, MEMORY.md, README.md)

Usage:
    python3 vault-style-audit.py                    # audit recently modified files (7 days)
    python3 vault-style-audit.py --all              # audit entire vault
    python3 vault-style-audit.py --path d359/       # audit a specific directory
    python3 vault-style-audit.py --fix              # auto-fix safe violations
    python3 vault-style-audit.py --since 2026-05-01 # audit files modified since date
"""

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

VAULT = Path.home() / "vault"
SKIP_DIRS = {".git", ".obsidian", "z_asts", "node_modules", ".trash"}
UPPER_OK = {"CLAUDE.md", "CONTEXT.md", "STYLE.md", "MEMORY.md", "README.md",
            "PURPOSE.md", "MIGRATION.md", "TODO.md", "CHANGELOG.md"}

# Frontmatter extraction
FM_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
FM_FIELD_RE = re.compile(r"^(\w[\w_-]*):\s*(.*)", re.MULTILINE)

# Style patterns
H1_RE = re.compile(r"^# .+", re.MULTILINE)
DATE_FILENAME_RE = re.compile(r"\d{4}-\d{2}-\d{2}")  # bad: hyphens in filename date
DATE_HEADING_RE = re.compile(r"^#{1,6}\s+.*\d{4}-\d{2}-\d{2}", re.MULTILINE)
TODO_RE = re.compile(r"^- \[ \]", re.MULTILINE)
EM_DASH_RE = re.compile(r"\u2014")  # —
D359_MEETING_RE = re.compile(r"^## \d{4}\.\d{2}\.\d{2}", re.MULTILINE)


def parse_frontmatter(text: str) -> dict[str, str]:
    m = FM_RE.match(text)
    if not m:
        return {}
    return dict(FM_FIELD_RE.findall(m.group(1)))


def body_after_frontmatter(text: str) -> str:
    m = FM_RE.match(text)
    return text[m.end():] if m else text


class StyleAuditor:
    def __init__(self, vault: Path):
        self.vault = vault
        self.violations: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.file_count = 0
        self.clean_count = 0

    def audit_file(self, path: Path):
        try:
            text = path.read_text(errors="replace")
        except Exception:
            return

        rel = str(path.relative_to(self.vault))
        self.file_count += 1
        found = False

        fm = parse_frontmatter(text)
        body = body_after_frontmatter(text)
        filename = path.name

        # Rule 1: No double title
        if fm.get("title") and H1_RE.search(body):
            h1_match = H1_RE.search(body)
            h1_text = h1_match.group(0)[2:].strip() if h1_match else ""
            fm_title = fm["title"].strip("'\"")
            # Only flag if the H1 is reasonably similar to the frontmatter title
            if (h1_text.lower().startswith(fm_title[:20].lower()) or
                    fm_title.lower().startswith(h1_text[:20].lower()) or
                    h1_text == fm_title):
                self._add(rel, "double-title", f"H1 '{h1_text}' duplicates frontmatter title")
                found = True

        # Rule 2: Filename dates should use dots not hyphens
        # Only check the date portion, not the slug after it
        name_stem = path.stem
        if DATE_FILENAME_RE.search(name_stem):
            # But allow if it's a non-date use of hyphens (kebab-case slug)
            # Check if the match is actually a date at the start
            m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", name_stem)
            if m:
                self._add(rel, "filename-date-hyphens",
                          f"Use YYYY.MM.DD not YYYY-MM-DD in filename")
                found = True

        # Rule 3: Frontmatter date fields should use ISO YYYY-MM-DD
        for field in ("date", "last_contact"):
            val = fm.get(field, "")
            if re.match(r"^\d{4}\.\d{2}\.\d{2}$", val):
                self._add(rel, "frontmatter-date-dots",
                          f"{field}: {val} should use YYYY-MM-DD (hyphens) in frontmatter")
                found = True

        # Rule 4: TODOs should be near the top
        todos = list(TODO_RE.finditer(body))
        if todos:
            first_todo_pos = todos[0].start()
            # If the first TODO is more than 500 chars into the body, it might be buried
            body_before_todo = body[:first_todo_pos]
            heading_count = len(re.findall(r"^#{1,6}\s", body_before_todo, re.MULTILINE))
            if heading_count >= 3:
                self._add(rel, "buried-todos",
                          f"TODOs appear after {heading_count} headings; move to top")
                found = True

        # Rule 5: d359 files structure
        if "d359/" in rel and rel.endswith("-d359.md"):
            meetings = list(D359_MEETING_RE.finditer(body))
            if meetings:
                first_meeting_pos = meetings[0].start()
                # Check there's a profile section before the first meeting
                body_before = body[:first_meeting_pos].strip()
                if len(body_before) < 20:
                    self._add(rel, "d359-no-profile",
                              "Meeting notes without a profile section at top")
                    found = True

        # Rule 6: No em-dashes
        em_dashes = list(EM_DASH_RE.finditer(body))
        if em_dashes:
            # Find line numbers
            lines_with_dashes = set()
            for md in em_dashes:
                line_num = body[:md.start()].count("\n") + 1
                lines_with_dashes.add(line_num)
            if len(lines_with_dashes) <= 5:
                line_list = ", ".join(str(n) for n in sorted(lines_with_dashes))
                self._add(rel, "em-dash", f"Em-dashes on lines {line_list}")
            else:
                self._add(rel, "em-dash", f"Em-dashes on {len(lines_with_dashes)} lines")
            found = True

        # Rule 7: Folder note convention
        if filename == path.parent.name + ".md":
            pass  # correct
        elif filename.endswith("-index.md"):
            self._add(rel, "index-suffix",
                      f"Use '{path.parent.name}.md' not '-index.md' suffix")
            found = True

        # Rule 8: kebab-case
        if filename not in UPPER_OK and not filename.startswith("."):
            stem = path.stem
            # Allow dates at start (YYYY.MM.DD-slug)
            slug = re.sub(r"^\d{4}[\.-]\d{2}[\.-]\d{2}-?", "", stem)
            if slug and not re.match(r"^[a-z0-9][a-z0-9-]*$", slug):
                # Allow CJK, numbers, and some special chars common in vault
                if re.search(r"[A-Z]", slug) and not any(ord(c) > 0x2E80 for c in slug):
                    self._add(rel, "not-kebab-case", f"Filename contains uppercase")
                    found = True

        if not found:
            self.clean_count += 1

    def _add(self, rel: str, rule: str, msg: str):
        self.violations[rule].append((rel, msg))

    def scan(self, path: Path | None = None, since: datetime | None = None):
        root = self.vault / path if path else self.vault
        for md in root.rglob("*.md"):
            if any(skip in md.parts for skip in SKIP_DIRS):
                continue
            # Skip the nested vault/vault/ duplicate tree
            try:
                rel = md.relative_to(self.vault)
                if str(rel).startswith("vault/"):
                    continue
            except ValueError:
                continue
            if "ai-transcripts" in str(md):
                continue  # skip AI transcripts
            if since and datetime.fromtimestamp(md.stat().st_mtime) < since.replace(tzinfo=None):
                continue
            self.audit_file(md)

    def report(self) -> str:
        lines = [f"# Vault Style Audit — {datetime.now():%Y.%m.%d %H:%M}",
                 f"",
                 f"Scanned {self.file_count} files. {self.clean_count} clean, "
                 f"{self.file_count - self.clean_count} with violations.",
                 f""]

        if not self.violations:
            lines.append("All clear.")
            return "\n".join(lines)

        # Summary table
        lines.append("| Rule | Count | Example |")
        lines.append("|------|------:|---------|")
        for rule, items in sorted(self.violations.items(), key=lambda x: -len(x[1])):
            example = items[0][0] if items else ""
            lines.append(f"| {rule} | {len(items)} | {example} |")
        lines.append("")

        # Details by rule
        for rule, items in sorted(self.violations.items(), key=lambda x: -len(x[1])):
            lines.append(f"## {rule} ({len(items)})")
            lines.append("")
            for rel, msg in items[:20]:
                lines.append(f"- `{rel}`: {msg}")
            if len(items) > 20:
                lines.append(f"- ... and {len(items) - 20} more")
            lines.append("")

        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Vault style auditor")
    parser.add_argument("--all", action="store_true", help="Audit entire vault")
    parser.add_argument("--path", type=str, help="Audit a specific subdirectory")
    parser.add_argument("--since", type=str, help="Audit files modified since YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=7,
                        help="Audit files modified in last N days (default: 7)")
    parser.add_argument("--output", type=str, help="Write report to file")
    args = parser.parse_args()

    auditor = StyleAuditor(VAULT)

    if args.all:
        since = None
    elif args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d")
    else:
        since = datetime.now() - timedelta(days=args.days)

    path = Path(args.path) if args.path else None
    auditor.scan(path=path, since=since)

    report = auditor.report()
    print(report)

    if args.output:
        Path(args.output).write_text(report)
        print(f"\nReport written to {args.output}")


if __name__ == "__main__":
    main()
