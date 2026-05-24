#!/usr/bin/env python3
"""
dream-vault-hygiene.py — Scan the vault for structural issues and output a report.

Checks:
  1. d359 files missing ## About section
  2. d358 files with stale updated: frontmatter
  3. o314 year index entry counts vs actual file counts
  4. Orphaned vault files (root or z_ibx/, >30 days old)
  5. d359 stale contacts (cadence vs last_contact)
  6. Duplicate d358 date headers
  7. Missing YAML frontmatter in key directories
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import yaml


def parse_frontmatter(text: str) -> Optional[dict]:
    """Extract YAML frontmatter from markdown text. Returns None if absent."""
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}


def relative(path: Path, vault: Path) -> str:
    try:
        return str(path.relative_to(vault))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Check 1: d359 files missing ## About
# ---------------------------------------------------------------------------

def check_missing_about(vault: Path) -> list[dict]:
    issues = []
    d359_dirs = [vault / "d359", vault / "h335" / "d359"]
    for d in d359_dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            # Only check files that look like d359 contact files
            if "d359" not in f.name.lower():
                continue
            text = f.read_text(errors="replace")
            if "## About" not in text:
                issues.append({
                    "type": "missing_about",
                    "file": relative(f, vault),
                    "severity": "medium",
                    "description": "d359 file has no ## About section",
                    "suggested_fix": "Add About section with role, context",
                })
    return issues


# ---------------------------------------------------------------------------
# Check 2: d358 files with stale updated: frontmatter
# ---------------------------------------------------------------------------

def check_stale_d358(vault: Path) -> list[dict]:
    issues = []
    d358_root = vault / "h335" / "d358"
    if not d358_root.is_dir():
        return issues

    heading_date_re = re.compile(r"^##\s+(\d{4}(?:[.-]\d{2}(?:[.-]\d{2})?)?)$", re.MULTILINE)

    for f in sorted(d358_root.rglob("*.md")):
        text = f.read_text(errors="replace")
        fm = parse_frontmatter(text)
        if not fm or "updated" not in fm:
            continue

        updated_raw = fm["updated"]
        if isinstance(updated_raw, datetime):
            updated_date = updated_raw.date() if hasattr(updated_raw, "date") else updated_raw
        elif isinstance(updated_raw, str):
            for fmt in ("%Y-%m-%d", "%Y.%m.%d"):
                try:
                    updated_date = datetime.strptime(updated_raw, fmt).date()
                    break
                except ValueError:
                    continue
            else:
                continue
        else:
            try:
                from datetime import date as date_cls
                if isinstance(updated_raw, date_cls):
                    updated_date = updated_raw
                else:
                    continue
            except Exception:
                continue

        # Find latest ## YYYY heading date
        matches = heading_date_re.findall(text)
        if not matches:
            continue

        latest_heading = None
        for m in matches:
            normalized = m.replace(".", "-")
            for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
                try:
                    d = datetime.strptime(normalized, fmt).date()
                    if latest_heading is None or d > latest_heading:
                        latest_heading = d
                    break
                except ValueError:
                    continue

        if latest_heading and (latest_heading - updated_date).days > 30:
            issues.append({
                "type": "stale_updated",
                "file": relative(f, vault),
                "severity": "low",
                "description": (
                    f"updated: {updated_date} is >{(latest_heading - updated_date).days}d "
                    f"older than latest heading date {latest_heading}"
                ),
                "suggested_fix": f"Update frontmatter updated: to {latest_heading}",
            })

    return issues


# ---------------------------------------------------------------------------
# Check 3: o314 year index counts
# ---------------------------------------------------------------------------

def check_o314_counts(vault: Path) -> list[dict]:
    issues = []
    o314 = vault / "hcmp" / "o314"
    if not o314.is_dir():
        return issues

    count_re = re.compile(r"\*\*(\d+)\s+entries?\*\*")

    for year_dir in sorted(o314.iterdir()):
        if not year_dir.is_dir():
            continue
        year_name = year_dir.name
        index_file = year_dir / f"{year_name}.md"
        if not index_file.is_file():
            continue

        text = index_file.read_text(errors="replace")
        m = count_re.search(text)
        if not m:
            continue

        claimed = int(m.group(1))
        actual = sum(1 for f in year_dir.glob("*.md") if f.name != f"{year_name}.md")

        if claimed != actual:
            issues.append({
                "type": "o314_count_mismatch",
                "file": relative(index_file, vault),
                "severity": "high",
                "description": f"Index claims {claimed} entries but folder has {actual}",
                "suggested_fix": f"Update entry count to {actual}",
            })

    return issues


# ---------------------------------------------------------------------------
# Check 4: Orphaned vault files
# ---------------------------------------------------------------------------

def check_orphaned(vault: Path) -> list[dict]:
    issues = []
    cutoff = datetime.now().timestamp() - 30 * 86400
    exclude_names = {
        "new-notes.md", "completed-today.json", "task-queue.json",
        "mtg-briefs.json", "mtg-postbriefs.json",
    }

    # Root .md files
    for f in vault.glob("*.md"):
        if f.name in exclude_names:
            continue
        if f.stat().st_mtime < cutoff:
            issues.append({
                "type": "orphaned_file",
                "file": relative(f, vault),
                "severity": "low",
                "description": "File in vault root is >30 days old",
                "suggested_fix": "File to correct domain folder or archive",
            })

    # z_ibx files (not in archive subfolder)
    z_ibx = vault / "z_ibx"
    if z_ibx.is_dir():
        for f in z_ibx.iterdir():
            if f.is_dir():
                continue
            if f.name in exclude_names:
                continue
            if not f.name.endswith((".md", ".json", ".jsonl")):
                continue
            if f.stat().st_mtime < cutoff:
                issues.append({
                    "type": "orphaned_file",
                    "file": relative(f, vault),
                    "severity": "low",
                    "description": "File in z_ibx is >30 days old",
                    "suggested_fix": "Process or archive this inbox item",
                })

    return issues


# ---------------------------------------------------------------------------
# Check 5: d359 stale contacts
# ---------------------------------------------------------------------------

CADENCE_DAYS = {
    "weekly": 7,
    "biweekly": 14,
    "monthly": 30,
    "quarterly": 90,
    "biannual": 180,
    "annual": 365,
}


def check_stale_contacts(vault: Path) -> list[dict]:
    issues = []
    today = datetime.now().date()
    d359_dirs = [vault / "d359", vault / "h335" / "d359"]

    for d in d359_dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            text = f.read_text(errors="replace")
            fm = parse_frontmatter(text)
            if not fm:
                continue

            cadence = fm.get("cadence")
            last_contact = fm.get("last_contact")
            if not cadence or not last_contact:
                continue

            cadence_str = str(cadence).lower().strip()
            if cadence_str not in CADENCE_DAYS:
                continue

            # Parse last_contact date
            if isinstance(last_contact, datetime):
                lc_date = last_contact.date()
            elif hasattr(last_contact, "year"):
                # datetime.date
                lc_date = last_contact
            elif isinstance(last_contact, str):
                for fmt in ("%Y-%m-%d", "%Y.%m.%d"):
                    try:
                        lc_date = datetime.strptime(last_contact, fmt).date()
                        break
                    except ValueError:
                        continue
                else:
                    continue
            else:
                continue

            due_date = lc_date + timedelta(days=CADENCE_DAYS[cadence_str])
            if due_date < today:
                overdue_days = (today - due_date).days
                issues.append({
                    "type": "stale_contact",
                    "file": relative(f, vault),
                    "severity": "medium",
                    "description": (
                        f"Contact overdue by {overdue_days}d "
                        f"(cadence: {cadence_str}, last: {lc_date})"
                    ),
                    "suggested_fix": f"Reach out or update last_contact / cadence",
                })

    return issues


# ---------------------------------------------------------------------------
# Check 6: Duplicate d358 date headers
# ---------------------------------------------------------------------------

def check_duplicate_d358_headers(vault: Path) -> list[dict]:
    issues = []
    d358_root = vault / "h335" / "d358"
    if not d358_root.is_dir():
        return issues

    date_heading_re = re.compile(r"^##\s+(\d{4}[.-]\d{2}[.-]\d{2})\b", re.MULTILINE)

    for f in sorted(d358_root.rglob("*.md")):
        text = f.read_text(errors="replace")
        dates = date_heading_re.findall(text)
        seen = {}
        for d in dates:
            norm = d.replace(".", "-")
            seen[norm] = seen.get(norm, 0) + 1
        for d, count in seen.items():
            if count > 1:
                issues.append({
                    "type": "duplicate_d358_header",
                    "file": relative(f, vault),
                    "severity": "high",
                    "description": f"Duplicate ## {d} heading appears {count} times",
                    "suggested_fix": "Merge or deduplicate entries for this date",
                })

    return issues


# ---------------------------------------------------------------------------
# Check 7: Missing frontmatter in key directories
# ---------------------------------------------------------------------------

def check_missing_frontmatter(vault: Path) -> list[dict]:
    issues = []
    key_dirs = ["h335", "hcmp", "g245", "m5x2", "s897", "xk87"]

    for dirname in key_dirs:
        d = vault / dirname
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*.md")):
            # Skip very small files or READMEs
            try:
                text = f.read_text(errors="replace")
            except Exception:
                continue
            if not text.strip():
                continue
            if not text.startswith("---"):
                issues.append({
                    "type": "missing_frontmatter",
                    "file": relative(f, vault),
                    "severity": "medium",
                    "description": "File lacks YAML frontmatter",
                    "suggested_fix": "Add frontmatter with title, date, type, tags",
                })

    return issues


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_report(vault: Path) -> dict:
    all_issues = []
    all_issues.extend(check_missing_about(vault))
    all_issues.extend(check_stale_d358(vault))
    all_issues.extend(check_o314_counts(vault))
    all_issues.extend(check_orphaned(vault))
    all_issues.extend(check_stale_contacts(vault))
    all_issues.extend(check_duplicate_d358_headers(vault))
    all_issues.extend(check_missing_frontmatter(vault))

    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for issue in all_issues:
        by_type[issue["type"]] = by_type.get(issue["type"], 0) + 1
        by_severity[issue["severity"]] = by_severity.get(issue["severity"], 0) + 1

    return {
        "generated": datetime.now().isoformat(),
        "issues": all_issues,
        "summary": {
            "total_issues": len(all_issues),
            "by_type": by_type,
            "by_severity": by_severity,
        },
    }


def print_summary(report: dict) -> None:
    s = report["summary"]
    print(f"Vault Hygiene Report — {report['generated']}")
    print(f"Total issues: {s['total_issues']}")
    print()

    if s["by_severity"].get("high"):
        print(f"  HIGH:   {s['by_severity']['high']}")
    if s["by_severity"].get("medium"):
        print(f"  MEDIUM: {s['by_severity']['medium']}")
    if s["by_severity"].get("low"):
        print(f"  LOW:    {s['by_severity']['low']}")
    print()

    for typ, count in sorted(s["by_type"].items()):
        print(f"  {typ}: {count}")
    print()

    # Print high-severity issues in detail
    high = [i for i in report["issues"] if i["severity"] == "high"]
    if high:
        print("--- HIGH severity ---")
        for i in high:
            print(f"  [{i['type']}] {i['file']}")
            print(f"    {i['description']}")
        print()

    medium = [i for i in report["issues"] if i["severity"] == "medium"]
    if medium:
        print(f"--- MEDIUM severity ({len(medium)} issues) ---")
        for i in medium[:20]:
            print(f"  [{i['type']}] {i['file']}")
            print(f"    {i['description']}")
        if len(medium) > 20:
            print(f"  ... and {len(medium) - 20} more")
        print()

    low = [i for i in report["issues"] if i["severity"] == "low"]
    if low:
        print(f"--- LOW severity ({len(low)} issues) ---")
        for i in low[:10]:
            print(f"  [{i['type']}] {i['file']}")
            print(f"    {i['description']}")
        if len(low) > 10:
            print(f"  ... and {len(low) - 10} more")


def main():
    parser = argparse.ArgumentParser(
        description="Check the vault for structural issues and output a report."
    )
    parser.add_argument(
        "--vault-path",
        type=Path,
        default=Path.home() / "vault",
        help="Path to the vault root (default: ~/vault/)",
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--json",
        action="store_true",
        default=True,
        help="Output JSON report (default)",
    )
    output_group.add_argument(
        "--summary",
        action="store_true",
        help="Output human-readable summary",
    )
    args = parser.parse_args()

    vault = args.vault_path.expanduser().resolve()
    if not vault.is_dir():
        print(f"Error: vault path {vault} is not a directory", file=sys.stderr)
        sys.exit(1)

    report = build_report(vault)

    if args.summary:
        print_summary(report)
    else:
        json.dump(report, sys.stdout, indent=2, default=str)
        print()


if __name__ == "__main__":
    main()
