#!/usr/bin/env python3
"""
Todoist Auto-Triage - Automatically route and estimate inbox tasks.

Pulls tasks from Todoist inbox (t779), auto-routes to correct projects based on
keywords/domain codes, and suggests time/分 estimates using fen.md rules.

Runs every 2 hours via GitHub Actions.

Usage:
    python3 todoist-auto-triage.py [--dry-run] [--verbose]
"""

import os
import re
import json
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import requests

# Todoist API
TODOIST_API_TOKEN = os.environ.get("TODOIST_API_KEY")
TODOIST_API_BASE = "https://api.todoist.com/rest/v2"

# Project mappings (domain code -> project ID)
PROJECT_MAPPING = {
    "inbox": "6Crfmq5PfP5VQ286",
    "i9": "6XQ3GMQRVmPgPM4W",
    "m5x2": "6Crfmq5Pjp462w3C",
    "f693": "6Crfmq5PxX3vQ58m",
    "f692": "6Crfmq5Pq7g95wMw",
    "f694": "6Crfmq5Q7QH7gfwJ",
    "i8": "6QVVMQ6HPqF4M7Wf",
    "q5n7": "6FgJq6cJ4cqr4Xg7",
    "g245": "6Crfmq5PpX4JhP4c",
    "qz12": "6Crfmq5Pw4Vc6rqF",
    "hcmp": "6Crfmq5PmCxPmf2V",
    "o314": "6Crfmq5QC2RGmwjp",
    "hcbi": "6Crfmq5PmcCPc7PC",
    "hci": "6Crfmq5PmPjqrx4x",
    "hcmc": "6PWgGPhJmxFp93Hf",
    "epcn": "6H2WF96ChxjvMRcr",
    "xk88": "6Crfmq5PpmP4jgfv",
    "xk87": "6Crfmq5QFg895Mcw",
    "s897": "6Crfmq5Pp27hV9qM",
}

# Routing rules (keyword/pattern -> project)
ROUTING_RULES = [
    # Career/Work
    (r'\b(microsoft|msft|i9|coreai|azure|ml)\b', 'i9'),
    (r'\b(interview|hiring|recruit)\b', 'i9'),

    # McKay Capital
    (r'\b(m5x2|mckay capital|fund|gp|lp|carry)\b', 'm5x2'),
    (r'\b(r202|r203|r888|h5c7|property|tenant|lease)\b', 'm5x2'),
    (r'\b(ian|leeroy|stefanie|andie)\b', 'm5x2'),

    # Finance
    (r'\b(qz12|finance|investment|portfolio|stock|401k)\b', 'qz12'),
    (r'\b(tax|ira|schwab|vanguard)\b', 'qz12'),

    # Goals & Reviews
    (r'\b(g245|goal|review|neon|分|fen)\b', 'g245'),
    (r'\b(5\^\d+\s*[sg]|checkin|sprint)\b', 'g245'),

    # Health & Mindfulness
    (r'\b(hcbi|health|fitness|sleep|diet|exercise)\b', 'hcbi'),
    (r'\b(hcmp|meditation|mindfulness|breathing)\b', 'hcmp'),
    (r'\b(o314|session|therapy)\b', 'o314'),

    # Media & Content
    (r'\b(hcmc|read|book|article|paper|video)\b', 'hcmc'),
    (r'\b(readwise|kindle|podcast)\b', 'hcmc'),

    # Family & Kids
    (r'\b(xk87|theo|ren|kids|school|curriculum)\b', 'xk87'),
    (r'\b(xk88|family|home|house)\b', 'xk88'),

    # Social
    (r'\b(s897|friend|social|party|event|visit)\b', 's897'),
]

# Time estimates (task patterns -> minutes)
TIME_ESTIMATES = [
    # Reviews and check-ins
    (r'\b5\^4\b', 60, 90),  # Quarterly review
    (r'\b5\^3\b', 30, 45),  # Monthly review
    (r'\b5\^2\b', 15, 20),  # Weekly review
    (r'\b5\^1\b', 5, 10),   # Daily check-in

    # Common actions
    (r'\b(email|send|reply|respond)\b', 5, 15),
    (r'\b(call|phone)\b', 10, 30),
    (r'\b(meeting|1:1|sync)\b', 30, 60),
    (r'\b(read|review)\b', 15, 45),
    (r'\b(write|draft|create)\b', 30, 90),
    (r'\b(research|investigate|explore)\b', 45, 120),
    (r'\b(fix|debug|troubleshoot)\b', 30, 90),
    (r'\b(plan|design|architect)\b', 45, 120),
]

# 分 scoring rules (based on fen.md)
FEN_RULES = {
    'critical_path': 100,  # [0G ...] tasks
    'quarterly_review': 90,
    'monthly_review': 60,
    'weekly_review': 40,
    'daily_review': 20,
    'high_value': 60,
    'medium_value': 30,
    'low_value': 10,
}


class TodoistTriager:
    def __init__(self, api_token: str, dry_run: bool = False, verbose: bool = False):
        self.api_token = api_token
        self.dry_run = dry_run
        self.verbose = verbose
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        self.stats = {
            "processed": 0,
            "routed": 0,
            "estimated": 0,
            "skipped": 0,
        }

    def run(self):
        """Run auto-triage on inbox tasks."""
        print("🔍 Fetching inbox tasks...")
        inbox_tasks = self._get_inbox_tasks()

        if not inbox_tasks:
            print("✅ Inbox is empty - nothing to triage!")
            return

        print(f"📥 Found {len(inbox_tasks)} tasks in inbox")

        for task in inbox_tasks:
            self._process_task(task)

        self._print_summary()

    def _get_inbox_tasks(self) -> List[Dict]:
        """Fetch all tasks from inbox project."""
        url = f"{TODOIST_API_BASE}/tasks"
        params = {"project_id": PROJECT_MAPPING["inbox"]}

        response = requests.get(url, headers=self.headers, params=params)
        response.raise_for_status()

        return response.json()

    def _process_task(self, task: Dict):
        """Process a single task: route, estimate, prioritize."""
        task_id = task["id"]
        content = task["content"]

        self.stats["processed"] += 1

        if self.verbose:
            print(f"\n📝 Processing: {content}")

        # Check if already has estimates
        has_time = re.search(r'\(\d+\)', content)
        has_fen = re.search(r'\[\d+\]', content)

        # Determine routing
        target_project = self._route_task(content)

        # Estimate time
        time_estimate = None
        if not has_time:
            time_estimate = self._estimate_time(content)

        # Estimate 分
        fen_estimate = None
        if not has_fen:
            fen_estimate = self._estimate_fen(content, time_estimate)

        # Build updates
        updates = {}

        # Update content with estimates
        new_content = content
        if time_estimate and not has_time:
            new_content = f"{new_content} ({time_estimate})"
        if fen_estimate and not has_fen:
            new_content = f"{new_content} [{fen_estimate}]"

        if new_content != content:
            updates["content"] = new_content
            self.stats["estimated"] += 1

        # Update project
        if target_project and target_project != "inbox":
            updates["project_id"] = PROJECT_MAPPING[target_project]
            self.stats["routed"] += 1

        # Set priority if missing
        if task.get("priority") == 1:  # p4 (default)
            # Determine priority from content
            if "[0G" in content or "**" in content:
                updates["priority"] = 4  # p1
            elif "urgent" in content.lower() or "asap" in content.lower():
                updates["priority"] = 3  # p2
            elif fen_estimate and fen_estimate >= 50:
                updates["priority"] = 3  # p2
            else:
                updates["priority"] = 2  # p3

        # Set due date to today if missing
        if not task.get("due"):
            updates["due_string"] = "today"

        # Apply updates
        if updates:
            if self.verbose:
                print(f"  → Route: {target_project or 'inbox'}")
                if time_estimate:
                    print(f"  → Time: {time_estimate}m")
                if fen_estimate:
                    print(f"  → 分: {fen_estimate}")

            if not self.dry_run:
                self._update_task(task_id, updates)
        else:
            self.stats["skipped"] += 1
            if self.verbose:
                print("  → No changes needed")

    def _route_task(self, content: str) -> Optional[str]:
        """Determine target project based on content."""
        content_lower = content.lower()

        # Check explicit domain codes first
        for domain_code in PROJECT_MAPPING.keys():
            if domain_code != "inbox" and f"{domain_code}" in content_lower:
                return domain_code

        # Apply routing rules
        for pattern, project in ROUTING_RULES:
            if re.search(pattern, content_lower, re.IGNORECASE):
                return project

        return None

    def _estimate_time(self, content: str) -> Optional[int]:
        """Estimate time in minutes based on content."""
        content_lower = content.lower()

        # Check time estimate patterns
        for pattern, min_time, max_time in TIME_ESTIMATES:
            if re.search(pattern, content_lower, re.IGNORECASE):
                # Return midpoint
                return (min_time + max_time) // 2

        # Default estimate based on length
        if len(content) < 30:
            return 15  # Quick task
        elif len(content) < 100:
            return 30  # Medium task
        else:
            return 60  # Detailed task

    def _estimate_fen(self, content: str, time_estimate: Optional[int]) -> int:
        """Estimate 分 value based on content and time."""
        # Check for critical path
        if "[0G" in content:
            return FEN_RULES['critical_path']

        # Check for review cadence
        if "5^4" in content:
            return FEN_RULES['quarterly_review']
        if "5^3" in content:
            return FEN_RULES['monthly_review']
        if "5^2" in content:
            return FEN_RULES['weekly_review']
        if "5^1" in content:
            return FEN_RULES['daily_review']

        # Estimate based on keywords
        content_lower = content.lower()
        if any(word in content_lower for word in ['critical', 'urgent', 'important', 'blocker']):
            return FEN_RULES['high_value']

        # Default: use time estimate
        if time_estimate:
            # 分 roughly equals minutes for most tasks
            return time_estimate

        return FEN_RULES['medium_value']

    def _update_task(self, task_id: str, updates: Dict):
        """Update a task via Todoist API."""
        url = f"{TODOIST_API_BASE}/tasks/{task_id}"

        response = requests.post(url, headers=self.headers, json=updates)
        response.raise_for_status()

    def _print_summary(self):
        """Print summary statistics."""
        print(f"\n✅ Triage complete!")
        print(f"  - Processed: {self.stats['processed']}")
        print(f"  - Routed: {self.stats['routed']}")
        print(f"  - Estimated: {self.stats['estimated']}")
        print(f"  - Skipped: {self.stats['skipped']}")


def main():
    parser = argparse.ArgumentParser(description="Auto-triage Todoist inbox tasks")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    if not TODOIST_API_TOKEN:
        print("❌ Error: TODOIST_API_TOKEN environment variable not set")
        return 1

    triager = TodoistTriager(TODOIST_API_TOKEN, dry_run=args.dry_run, verbose=args.verbose)
    triager.run()


if __name__ == "__main__":
    main()
