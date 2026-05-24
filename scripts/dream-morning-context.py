#!/usr/bin/env python3
"""Generate pre-computed morning context from yesterday's data sources.

Pulls Toggl entries, completed Todoist tasks, vault file changes, and build
order state into a single compact markdown file for the first session of the day.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
VAULT = Path.home() / "vault"
DEFAULT_OUTPUT = VAULT / "i447/i446/dream-runs/morning-context-latest.md"
TOGGL_CLI = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"
BUILD_ORDER = VAULT / "g245/-1₦ , 0₦ - Neon {Build Order}.md"
COMPLETED_JSON = VAULT / "z_ibx/completed-today.json"

# Domain prefixes for categorizing vault changes
DOMAIN_MAP = {
    "d359": "people/CRM",
    "g245": "goals",
    "h335": "career",
    "m5x2": "McKay Capital",
    "qz12": "finance",
    "hcmc": "media",
    "hcmp": "mindfulness",
    "hcbi": "health",
    "xk88": "social",
    "xk87": "social",
    "s897": "social",
    "i447": "infrastructure",
    "i446": "infrastructure",
    "o314": "journal",
    "z_ibx": "inbox",
}


def resolve_date(date_str: str) -> datetime.date:
    """Parse date string; supports 'yesterday' and ISO dates."""
    if date_str == "yesterday":
        return datetime.datetime.now(TZ).date() - datetime.timedelta(days=1)
    return datetime.date.fromisoformat(date_str)


def get_toggl_entries(target_date: datetime.date) -> dict:
    """Fetch Toggl entries for target_date using the toggl_api module directly."""
    # Add the toggl_server parent to path so imports work
    toggl_parent = str(Path.home() / "i446-monorepo/mcp")
    if toggl_parent not in sys.path:
        sys.path.insert(0, toggl_parent)

    # Load API key from ~/.claude.json if not set
    if not os.environ.get("TOGGL_API_KEY"):
        try:
            with open(Path.home() / ".claude.json") as f:
                d = json.load(f)
            key = (d.get("mcpServers", {})
                     .get("toggl_server", {})
                     .get("env", {})
                     .get("TOGGL_API_KEY", ""))
            if key:
                os.environ["TOGGL_API_KEY"] = key
        except Exception:
            pass
    os.environ.setdefault("TOGGL_WORKSPACE_ID", "2092616")

    from toggl_server import toggl_api
    from toggl_server.config import PROJECT_NAMES

    raw = toggl_api.get_entries(
        start_date=(target_date - datetime.timedelta(days=1)).isoformat(),
        end_date=(target_date + datetime.timedelta(days=2)).isoformat(),
    ) or []

    entries = []
    for e in raw:
        try:
            st = datetime.datetime.fromisoformat(e.get("start", "")).astimezone(TZ)
            if st.date() == target_date:
                entries.append(e)
        except Exception:
            continue

    entries.sort(key=lambda e: e.get("start", ""))
    total_sec = sum(e.get("duration", 0) for e in entries if e.get("duration", 0) > 0)

    # Breakdown by project
    by_project: dict[str, int] = {}
    longest = None
    longest_dur = 0
    for e in entries:
        dur = e.get("duration", 0)
        if dur <= 0:
            continue
        proj_id = e.get("project_id")
        proj_name = PROJECT_NAMES.get(proj_id, "untracked") if proj_id else "untracked"
        by_project[proj_name] = by_project.get(proj_name, 0) + dur
        if dur > longest_dur:
            longest_dur = dur
            longest = e.get("description", "(no description)")

    return {
        "total_hours": round(total_sec / 3600, 1),
        "count": len(entries),
        "by_project": {k: round(v / 3600, 1) for k, v in sorted(by_project.items(), key=lambda x: -x[1])},
        "longest": longest,
        "longest_hours": round(longest_dur / 3600, 1) if longest_dur else 0,
    }


def get_completed_tasks(target_date: datetime.date) -> list[str]:
    """Read completed tasks from completed-today.json or return empty list."""
    try:
        with open(COMPLETED_JSON) as f:
            data = json.load(f)
        file_date = data.get("date", "")
        # Only use if the file matches our target date
        if file_date == target_date.isoformat():
            return data.get("names", [])
        # If the file is for a different date, return what we have with a note
        # (the file may have been updated for today already)
        return data.get("names", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def get_vault_changes(target_date: datetime.date) -> dict[str, int]:
    """Find vault markdown files modified on target_date, categorized by domain."""
    start = datetime.datetime(target_date.year, target_date.month, target_date.day, tzinfo=TZ)
    end = start + datetime.timedelta(days=1)

    # Create reference files for find -newer
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ref") as f1:
        start_ref = f1.name
        os.utime(start_ref, (start.timestamp(), start.timestamp()))
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ref") as f2:
        end_ref = f2.name
        os.utime(end_ref, (end.timestamp(), end.timestamp()))

    try:
        result = subprocess.run(
            ["find", str(VAULT), "-name", "*.md", "-newer", start_ref, "!", "-newer", end_ref],
            capture_output=True, text=True, timeout=10,
        )
        files = [f for f in result.stdout.strip().split("\n") if f]
    except (subprocess.TimeoutExpired, Exception):
        files = []
    finally:
        os.unlink(start_ref)
        os.unlink(end_ref)

    # Categorize by domain
    by_domain: dict[str, int] = {}
    vault_str = str(VAULT) + "/"
    for fpath in files:
        rel = fpath.replace(vault_str, "", 1) if fpath.startswith(vault_str) else fpath
        # Match first path component to domain
        first_part = rel.split("/")[0]
        domain = None
        for code, label in DOMAIN_MAP.items():
            if first_part.startswith(code) or first_part == code:
                domain = code
                break
        if not domain:
            domain = first_part
        by_domain[domain] = by_domain.get(domain, 0) + 1

    return by_domain


def get_build_order() -> list[dict]:
    """Extract 0g goals from build order file (checked/unchecked)."""
    try:
        text = BUILD_ORDER.read_text()
    except FileNotFoundError:
        return []

    goals = []
    in_0g = False
    for line in text.splitlines():
        if line.strip().startswith("## 0₲"):
            in_0g = True
            continue
        if in_0g:
            if line.strip().startswith("##") or line.strip().startswith("###"):
                break
            m = re.match(r"\s*- \[([ x])\] (.+)", line)
            if m:
                goals.append({"done": m.group(1) == "x", "text": m.group(2).strip()})

    return goals


def generate_context(target_date: datetime.date, output_path: Path) -> None:
    """Generate the morning context markdown file."""
    now = datetime.datetime.now(TZ)
    day_name = target_date.strftime("%A")
    date_short = f"{target_date.month}/{target_date.day}"

    # Gather data
    toggl = get_toggl_entries(target_date)
    completed = get_completed_tasks(target_date)
    vault_changes = get_vault_changes(target_date)
    goals_0g = get_build_order()

    # Build markdown
    lines = [
        "---",
        f"title: Morning Context",
        f"date: {now.strftime('%Y-%m-%d')}",
        f"generated: {now.isoformat()}",
        "---",
        "",
        f"## Yesterday ({day_name}, {date_short})",
        "",
    ]

    # Time section
    project_parts = " | ".join(f"{k}: {v}h" for k, v in toggl["by_project"].items())
    lines.append(f"**Time:** {toggl['total_hours']}h tracked across {toggl['count']} entries")
    if project_parts:
        lines.append(f"- {project_parts}")
    if toggl["longest"]:
        lines.append(f"- Longest: {toggl['longest']} ({toggl['longest_hours']}h)")
    lines.append("")

    # Completed tasks
    lines.append(f"**Completed:** {len(completed)} tasks")
    for name in completed[:10]:
        lines.append(f"- {name}")
    if len(completed) > 10:
        lines.append(f"- ...and {len(completed) - 10} more")
    lines.append("")

    # Vault changes
    total_files = sum(vault_changes.values())
    lines.append(f"**Vault changes:** {total_files} files modified")
    for domain, count in sorted(vault_changes.items(), key=lambda x: -x[1]):
        lines.append(f"- {domain}: {count} files")
    lines.append("")

    # Today's starting state
    lines.append("## Today's Starting State")
    lines.append("")

    # 0g goals
    if goals_0g:
        goal_parts = []
        for g in goals_0g:
            check = "[x]" if g["done"] else "[ ]"
            goal_parts.append(f"  - {check} {g['text']}")
        lines.append("**0g goals:**")
        lines.extend(goal_parts)
    else:
        lines.append("**0g goals:** (none found)")
    lines.append("")

    # Dream cards placeholder
    lines.append("## Dream Cards Pending")
    lines.append("[placeholder for Dream to fill with its morning brief cards]")
    lines.append("")

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    print(f"Morning context written to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate morning context from yesterday's data")
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=str(DEFAULT_OUTPUT),
        help="Output file path (default: ~/vault/i447/i446/dream-runs/morning-context-latest.md)",
    )
    parser.add_argument(
        "--date", "-d",
        type=str,
        default="yesterday",
        help="Target date: 'yesterday' or YYYY-MM-DD (default: yesterday)",
    )
    args = parser.parse_args()

    target_date = resolve_date(args.date)
    output_path = Path(os.path.expanduser(args.output))

    generate_context(target_date, output_path)


if __name__ == "__main__":
    main()
