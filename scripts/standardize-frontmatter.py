#!/usr/bin/env python3
"""
Frontmatter Standardization Script for Inkwell Vault

This script adds missing required fields, standardizes formats, and adds domain-specific
frontmatter fields to markdown files in the Obsidian vault.

Usage:
    python standardize-frontmatter.py [options]

Options:
    --dry-run               Show what would change without making changes
    --domain DOMAIN         Only process files in specified domain
    --fix-dates             Fix date format inconsistencies
    --add-status            Add status field to all files
    --add-updated           Add updated field with file mtime
    --validate-archive      Check z_arcv files have status: archived
    --verbose               Show detailed output
    --help                  Show this help message

Examples:
    # Dry run on all files
    python standardize-frontmatter.py --dry-run

    # Add status to xk87 domain only
    python standardize-frontmatter.py --domain xk87 --add-status

    # Fix all issues in g245
    python standardize-frontmatter.py --domain g245 --fix-dates --add-status --add-updated

    # Validate z_arcv files
    python standardize-frontmatter.py --validate-archive --dry-run
"""

import os
import sys
import re
import argparse
from datetime import datetime
from pathlib import Path
from collections import Counter, defaultdict

# Default vault path
VAULT_PATH = os.path.expanduser("~/vault")

# Required fields for all files
REQUIRED_FIELDS = ['title', 'date', 'type', 'tags', 'source']

# Domain-specific field mappings
DOMAIN_FIELDS = {
    'm5x2': {
        'optional_fields': ['property', 'fund', 'fiscal_year', 'quarter'],
        'property_codes': ['w226', 'r888', 'r202', 'r203', 'h5c7', 'a621', 'a627', 'b735'],
    },
    'i9': {
        'required_fields': ['tenure'],
        'optional_fields': ['project', 'team', 'meeting_type', 'stakeholder'],
        'tenure_value': 'microsoft',
    },
    'xk87': {
        'optional_fields': ['child', 'curriculum_level', 'subject'],
        'child_values': ['theo', 'ren', 'both'],
    },
    'g245': {
        'optional_fields': ['goal_year', 'review_type', 'review_period', 'neon_version'],
        'review_types': ['weekly', 'monthly', 'quarterly', 'annual'],
    },
}

class FrontmatterStandardizer:
    def __init__(self, vault_path, dry_run=False, verbose=False):
        self.vault_path = Path(vault_path)
        self.dry_run = dry_run
        self.verbose = verbose

        # Statistics
        self.stats = {
            'files_processed': 0,
            'files_modified': 0,
            'fields_added': Counter(),
            'errors': [],
        }

    def log(self, message, level='info'):
        """Log message if verbose or if error"""
        if level == 'error' or self.verbose:
            prefix = {
                'info': 'ℹ',
                'warn': '⚠',
                'error': '✗',
                'success': '✓',
            }.get(level, ' ')
            print(f"{prefix} {message}")

    def extract_frontmatter(self, content):
        """Extract YAML frontmatter from markdown content"""
        if not content.startswith('---'):
            return None, content

        parts = content.split('---', 2)
        if len(parts) < 3:
            return None, content

        return parts[1].strip(), parts[2]

    def parse_frontmatter(self, fm_text):
        """Parse frontmatter into ordered dict preserving structure"""
        fields = {}
        if not fm_text:
            return fields

        for line in fm_text.split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                fields[key.strip()] = value.strip()

        return fields

    def build_frontmatter(self, fields):
        """Build YAML frontmatter from dict"""
        lines = ['---']
        for key, value in fields.items():
            lines.append(f"{key}: {value}")
        lines.append('---')
        return '\n'.join(lines)

    def infer_title(self, filepath):
        """Infer title from filename"""
        filename = filepath.stem
        # Convert kebab-case to Title Case
        title = filename.replace('-', ' ').replace('_', ' ')
        # Capitalize each word
        title = ' '.join(word.capitalize() for word in title.split())
        return f'"{title}"'

    def get_file_date(self, filepath):
        """Get file modification date in YYYY-MM-DD format"""
        mtime = filepath.stat().st_mtime
        return datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')

    def infer_type(self, filepath, content):
        """Infer document type from filename and content"""
        name = filepath.stem.lower()

        # Check filename patterns
        if 'meta' in name:
            return 'meta'
        if 'index' in name:
            return 'index'
        if 'readme' in name:
            return 'index'
        if name.endswith('-sop'):
            return 'sop'
        if 'roadmap' in name:
            return 'roadmap'
        if 'strategy' in name or 'strat' in name:
            return 'strategy'
        if 'review' in name:
            return 'review'
        if 'meeting' in name or '1-1' in name:
            return 'meeting-notes'
        if 'curriculum' in name:
            return 'curriculum'

        # Default to 'doc'
        return 'doc'

    def get_domain(self, filepath):
        """Get domain code from file path"""
        rel_path = filepath.relative_to(self.vault_path)
        parts = str(rel_path).split('/')
        return parts[0] if len(parts) > 1 else 'root'

    def infer_tags(self, filepath, domain):
        """Infer tags from domain and path"""
        tags = [domain]

        # Add subdomain tags for h335
        if domain == 'h335':
            rel_path = str(filepath.relative_to(self.vault_path))
            if '/i9/' in rel_path:
                tags.extend(['i9', 'microsoft'])
            elif '/m5x2/' in rel_path:
                tags.append('m5x2')
            elif '/f693/' in rel_path:
                tags.append('f693')

        # Add specific tags for other domains
        if domain == 'g245' and 'neon' in filepath.stem.lower():
            tags.append('neon')
        if domain == 'xk87' and 'curriculum' in filepath.stem.lower():
            tags.append('curriculum')

        return f"[{', '.join(tags)}]"

    def add_domain_specific_fields(self, fields, filepath, domain):
        """Add domain-specific fields based on file location and content"""
        added = []

        # i9: Add tenure field for Microsoft work
        if domain == 'h335' and '/i9/' in str(filepath):
            if 'tenure' not in fields:
                fields['tenure'] = 'microsoft'
                added.append('tenure')

        # xk87: Infer child from path or filename
        if domain == 'xk87':
            name_lower = filepath.stem.lower()
            if 'theo' in name_lower and 'child' not in fields:
                fields['child'] = 'theo'
                added.append('child')
            elif 'ren' in name_lower and 'child' not in fields:
                fields['child'] = 'ren'
                added.append('child')

            # Infer curriculum level from filename
            if match := re.search(r'pk(\d)', name_lower):
                if 'curriculum_level' not in fields:
                    fields['curriculum_level'] = f'pk{match.group(1)}'
                    added.append('curriculum_level')
            elif match := re.search(r'grade-?(\d+)', name_lower):
                if 'curriculum_level' not in fields:
                    fields['curriculum_level'] = match.group(1)
                    added.append('curriculum_level')

        # m5x2: Infer property code from path
        if domain == 'h335' and '/m5x2/' in str(filepath):
            rel_path = str(filepath.relative_to(self.vault_path))
            # Check if file is in a property folder
            for code in DOMAIN_FIELDS['m5x2']['property_codes']:
                if f'/{code}/' in rel_path:
                    if 'property' not in fields:
                        fields['property'] = code
                        added.append('property')
                    break

            # Check if in fund folder
            if '/fund-0/' in rel_path and 'fund' not in fields:
                fields['fund'] = 'fund-0'
                added.append('fund')
            elif '/fund-i/' in rel_path and 'fund' not in fields:
                fields['fund'] = 'fund-i'
                added.append('fund')

        # g245: Infer review type and neon version
        if domain == 'g245':
            name_lower = filepath.stem.lower()
            if 'review' in name_lower and 'review_type' not in fields:
                if 'weekly' in name_lower or re.search(r'w\d{2}', name_lower):
                    fields['review_type'] = 'weekly'
                    added.append('review_type')
                elif 'monthly' in name_lower:
                    fields['review_type'] = 'monthly'
                    added.append('review_type')
                elif 'quarterly' in name_lower or 'q1' in name_lower or 'q2' in name_lower:
                    fields['review_type'] = 'quarterly'
                    added.append('review_type')

            # Infer neon version from filename
            if match := re.search(r'v(\d+)\.(\d+)', name_lower):
                if 'neon_version' not in fields:
                    fields['neon_version'] = f'v{match.group(1)}.{match.group(2)}'
                    added.append('neon_version')

        return added

    def standardize_file(self, filepath, options):
        """Standardize frontmatter for a single file"""
        try:
            # Read file
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

            # Extract frontmatter
            fm_text, body = self.extract_frontmatter(content)

            # Parse existing fields
            if fm_text:
                fields = self.parse_frontmatter(fm_text)
            else:
                fields = {}
                self.log(f"Creating frontmatter for {filepath.name}", 'info')

            # Track changes
            changes = []
            domain = self.get_domain(filepath)

            # Add missing required fields
            if 'title' not in fields:
                fields['title'] = self.infer_title(filepath)
                changes.append('Added title')

            if 'date' not in fields:
                fields['date'] = self.get_file_date(filepath)
                changes.append('Added date')
            elif options.get('fix_dates'):
                # Fix date format if needed
                date_val = fields['date'].strip('"\'')
                if date_val and not re.match(r'^\d{4}-\d{2}-\d{2}$', date_val):
                    # Skip templates with placeholders
                    if not date_val.startswith('<'):
                        fields['date'] = self.get_file_date(filepath)
                        changes.append('Fixed date format')

            if 'type' not in fields:
                fields['type'] = self.infer_type(filepath, content)
                changes.append('Added type')

            if 'tags' not in fields:
                fields['tags'] = self.infer_tags(filepath, domain)
                changes.append('Added tags')

            if 'source' not in fields:
                fields['source'] = 'manual'
                changes.append('Added source')

            # Add status field
            if options.get('add_status') and 'status' not in fields:
                if domain == 'z_arcv':
                    fields['status'] = 'archived'
                else:
                    fields['status'] = 'active'
                changes.append('Added status')

            # Add updated field
            if options.get('add_updated'):
                fields['updated'] = self.get_file_date(filepath)
                changes.append('Added/updated updated field')

            # Add domain-specific fields
            domain_fields_added = self.add_domain_specific_fields(fields, filepath, domain)
            if domain_fields_added:
                changes.extend([f'Added {f}' for f in domain_fields_added])

            # Validate archive status
            if options.get('validate_archive') and domain == 'z_arcv':
                if fields.get('status', '').strip('"\'').lower() != 'archived':
                    fields['status'] = 'archived'
                    changes.append('Fixed archive status')

            # If no changes, skip
            if not changes:
                return None

            # Build new frontmatter
            new_fm = self.build_frontmatter(fields)
            new_content = f"{new_fm}\n{body}"

            # Write file if not dry run
            if not self.dry_run:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                self.log(f"Modified: {filepath.relative_to(self.vault_path)}", 'success')
            else:
                self.log(f"Would modify: {filepath.relative_to(self.vault_path)}", 'info')

            # Update stats
            for change in changes:
                self.stats['fields_added'][change] += 1

            return changes

        except Exception as e:
            self.log(f"Error processing {filepath}: {e}", 'error')
            self.stats['errors'].append((str(filepath), str(e)))
            return None

    def process_vault(self, options):
        """Process all files in vault"""
        domain_filter = options.get('domain')

        # Walk vault
        for root, dirs, files in os.walk(self.vault_path):
            # Skip certain directories
            if any(skip in root for skip in ['node_modules', '.git', 'z_asts']):
                continue

            for filename in files:
                if not filename.endswith('.md'):
                    continue

                filepath = Path(root) / filename

                # Apply domain filter
                if domain_filter:
                    domain = self.get_domain(filepath)
                    if domain != domain_filter:
                        continue

                self.stats['files_processed'] += 1

                # Standardize file
                changes = self.standardize_file(filepath, options)
                if changes:
                    self.stats['files_modified'] += 1

        # Print summary
        self.print_summary()

    def print_summary(self):
        """Print statistics summary"""
        print("\n" + "="*60)
        print("STANDARDIZATION SUMMARY")
        print("="*60)
        print(f"Files processed: {self.stats['files_processed']:,}")
        print(f"Files modified: {self.stats['files_modified']:,}")

        if self.stats['fields_added']:
            print("\nChanges made:")
            for change, count in sorted(self.stats['fields_added'].items(), key=lambda x: -x[1]):
                print(f"  {change}: {count:,} files")

        if self.stats['errors']:
            print(f"\nErrors encountered: {len(self.stats['errors'])}")
            for path, error in self.stats['errors'][:10]:
                print(f"  {path}: {error}")
            if len(self.stats['errors']) > 10:
                print(f"  ...and {len(self.stats['errors']) - 10} more errors")

        if self.dry_run:
            print("\n⚠ DRY RUN MODE - No files were actually modified")

        print("="*60)

def main():
    parser = argparse.ArgumentParser(
        description='Standardize frontmatter in Obsidian vault markdown files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would change without making changes')
    parser.add_argument('--domain', type=str,
                       help='Only process files in specified domain')
    parser.add_argument('--fix-dates', action='store_true',
                       help='Fix date format inconsistencies')
    parser.add_argument('--add-status', action='store_true',
                       help='Add status field to all files')
    parser.add_argument('--add-updated', action='store_true',
                       help='Add updated field with file mtime')
    parser.add_argument('--validate-archive', action='store_true',
                       help='Check z_arcv files have status: archived')
    parser.add_argument('--verbose', action='store_true',
                       help='Show detailed output')
    parser.add_argument('--vault-path', type=str, default=VAULT_PATH,
                       help=f'Path to vault (default: {VAULT_PATH})')

    args = parser.parse_args()

    # Build options dict
    options = {
        'fix_dates': args.fix_dates,
        'add_status': args.add_status,
        'add_updated': args.add_updated,
        'validate_archive': args.validate_archive,
        'domain': args.domain,
    }

    # Create standardizer
    standardizer = FrontmatterStandardizer(
        vault_path=args.vault_path,
        dry_run=args.dry_run,
        verbose=args.verbose
    )

    # Process vault
    print(f"Processing vault at: {args.vault_path}")
    if args.domain:
        print(f"Domain filter: {args.domain}")
    if args.dry_run:
        print("DRY RUN MODE - No files will be modified")
    print()

    standardizer.process_vault(options)

if __name__ == '__main__':
    main()
