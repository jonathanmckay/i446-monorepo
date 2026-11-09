#!/usr/bin/env python3
"""
0g-sync.py - Sync daily 0g goals between markdown and Todoist.

Source of truth: ~/vault/g245/5e-1/build-order.md
Todoist project: 0g (ID: 6XfvCQ3p8Gq6fhGR)

Modes:
  sync    - Bidirectional: new MD tasks -> Todoist, Todoist completions -> remove from MD
  cleanup - Move remaining unchecked 0g items to yihou section, clear Todoist

Usage:
  python3 0g-sync.py sync [--dry-run] [--verbose]
  python3 0g-sync.py cleanup [--dry-run] [--verbose]
"""

import os
import re
import sys
import json
import subprocess
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, NamedTuple

try:
    import requests
except ImportError:
    print("Error: requests library required. Install with: pip3 install --user requests")
    sys.exit(1)

# --- Constants ---

MD_FILE = Path.home() / "vault/g245/5e-1/build-order.md"
STATE_FILE = Path(__file__).resolve().parent / ".0g-sync-state.json"
LOG_FILE = Path(__file__).resolve().parent / ".0g-sync.log"

TODOIST_API_BASE = "https://api.todoist.com/api/v1"
TODOIST_0G_PROJECT_ID = "6XfvCQ3p8Gq6fhGR"

# Domain label → the Todoist project a 0g goal is filed in (JM 2026-11-09:
# "once I make goals, infer the project and add that as the 'project' for the
# tasks"). Ids from find-projects 2026-11-09. A goal whose domain is unknown or
# unmapped stays in the 0g project. Every path that reads 0g goals back
# (run_sync, run_cleanup, dtd, -2n) selects by the #0g label, never by project.
DOMAIN_PROJECT = {
    "i9": "6XQ3GMQRVmPgPM4W", "m5x2": "6Crfmq5Pjp462w3C", "g245": "6Crfmq5PpX4JhP4c",
    "qz12": "6Crfmq5Pw4Vc6rqF", "hcm": "6Crfmq5PmCxPmf2V", "hcb": "6Crfmq5PmcCPc7PC",
    "hcbp": "6Crfmq5Q33gJ5Q3P", "hcbc": "6Crfmq5Q3CHgFCpV", "hci": "6Crfmq5PmPjqrx4x",
    "hcmc": "6PWgGPhJmxFp93Hf", "xk88": "6Crfmq5PpmP4jgfv", "xk87": "6Crfmq5QFg895Mcw",
    "s897": "6Crfmq5Pp27hV9qM", "家": "6Crfmq5PjWxHRfpx", "i447": "6Crfmq5Pqm2PFQFc",
    "i444": "6M6RxxQrj72pRcM7", "f693": "6Crfmq5PxX3vQ58m", "f692": "6Crfmq5Pq7g95wMw",
    "f694": "6Crfmq5Q7QH7gfwJ", "epcn": "6H2WF96ChxjvMRcr", "m828": "6Crfmq5Q2M3wgqFH",
    "o314": "6Crfmq5QC2RGmwjp", "n156": "6Crfmq5PrJX6V2Cw", "q5n7": "6FgJq6cJ4cqr4Xg7",
    "i8": "6QVVMQ6HPqF4M7Wf",
}


def project_for(domain: Optional[str]) -> str:
    """Todoist project for a goal's domain; the 0g project when unknown."""
    return DOMAIN_PROJECT.get(domain or "", TODOIST_0G_PROJECT_ID)

# Matches: - [ ] description {60}  or  - [ ] description <60>
TASK_RE = re.compile(
    r"^(\s*-\s*\[)([ xX])(\]\s+)"  # checkbox
    r"(.+?)"                        # description (non-greedy)
    r"(?:\s*(?:\{(\d+)\}|<(\d+)>))?" # optional {N} or <N>
    r"\s*$"
)

OG_HEADING = "\u0030\u20b2"  # "0" + "₲"
LATER_HEADING = "\u4ee5\u540e\u7684\u76ee\u6807"  # 以后的目标

OG_LABEL = "#0g"
NEG1_LABEL = "#-1g"
CRITICAL_PATH_LABEL = "#关键径路"  # #关键径路
NEG1_HEADING = "-1₲"  # -1₲

# Match any markdown task line at any indent.
# Group 1: indent + "- [", group 2: state char, group 3: "] " + space, group 4: description
CHECKBOX_RE = re.compile(r"^(\s*-\s*\[)([ xX])(\]\s+)(.+)$")


def flip_to_checked(line: str) -> str:
    """Idempotently flip `- [ ]` to `- [x]` on a task line. Returns unchanged if not unchecked."""
    m = CHECKBOX_RE.match(line)
    if not m or m.group(2).strip() != "":
        return line
    return f"{m.group(1)}x{m.group(3)}{m.group(4)}"


# Domain detection rules: (pattern, label). First match wins, so order is
# specificity: explicit codes, then the domain whose keywords are least
# ambiguous. Single source of truth for 0g goal routing — the /0g skill calls
# `0g-sync.py route` instead of carrying its own copy (2026-11-09; the two
# tables had drifted: this one emitted hcbi/hcmp, which did-fast's
# LABEL_TO_0FEN does not know, and sent family/home goals to xk88).
DOMAIN_RULES = [
    # Explicit domain codes (highest priority; "@i9" matches too)
    (r'\bi9\b', 'i9'),
    (r'\bm5x2\b', 'm5x2'),
    (r'\bg245\b', 'g245'),
    (r'\bqz12\b', 'qz12'),
    (r'\b(hcmp|hcm)\b', 'hcm'),
    (r'\bhcbp\b', 'hcbp'),
    (r'\bhcbc\b', 'hcbc'),
    (r'\b(hcbi|hcb)\b', 'hcb'),
    (r'\bhci\b', 'hci'),
    (r'\bhcmc\b', 'hcmc'),
    (r'\bxk88\b', 'xk88'),
    (r'\bxk87\b', 'xk87'),
    (r'\bs897\b', 's897'),
    (r'\bi447\b', 'i447'),
    (r'\bi444\b', 'i444'),
    (r'\bf693\b', 'f693'),
    (r'\bf694\b', 'f694'),
    (r'\bf692\b', 'f692'),
    (r'\bo314\b', 'o314'),
    (r'\bepcn\b', 'epcn'),
    (r'\bm828\b', 'm828'),
    (r'\bq5n7\b', 'q5n7'),
    (r'\bi8\b', 'i8'),
    (r'\bn156\b', 'n156'),
    (r'家', '家'),
    # Keyword-based detection
    (r'\b(mckay capital|fund|tenant|lease|property|appfolio|occupancy|maintenance|p&l|'
     r'bookkeeping|eviction|renewal|vacancy|r20\d|ps\d+|rl\d+|l912|h604|'
     r'ian|leeroy|stefanie|andie|louisa|olga|lx|hz)\b', 'm5x2'),
    (r'\b(microsoft|msft|copilot|coreai|azure|github|xbox|platform|teams|metrics|standup|'
     r'sprint|retro|slt|1:1|pr|deploy|pipeline|experiments?|experimentation|forza|halo|'
     r'activation|exp)\b', 'i9'),
    (r'\b(theo|ren|aurora|rori|kids|school|math|homework|reading|lego|ptc|curriculum|'
     r'family|home|house|ashan|gi)\b', 'xk87'),
    (r'\b(exercise|gym|hiit|bball|basketball|yoga|stretch|legs|workout|fitness|'
     r'running|lift|weights)\b', 'hcbp'),
    (r'\b(eat|food|meal|calories|nutrition|breakfast|lunch|dinner|snack|health|sleep|'
     r'diet|weight|cpap)\b', 'hcb'),
    (r'\b(meditat\w*|mindfulness|breathing|journal|prayer|reflect|dream)\b|冥想', 'hcm'),
    (r'\b(news|youtube|podcast|article|book|kindle|read)\b|新闻', 'hcmc'),
    (r'\b(invest\w*|stocks?|portfolio|taxes|tax|budget|finance|401k|ira|schwab|'
     r'vanguard)\b', 'qz12'),
    (r'\b(social|friends?|party|visit|dinner out)\b', 's897'),
    (r'\b(admin|tooling|setup|infra|claude|system|dtd|janus|vault|skill|cron|ledger|'
     r'neon)\b', 'i447'),
    (r'\b(goal|review|plan|weekly|1s|checkin|points)\b|分', 'g245'),
]


def detect_domain(description: str) -> Optional[str]:
    """First DOMAIN_RULES match for a goal's text, or None."""
    desc_lower = (description or "").lower()
    for pattern, label in DOMAIN_RULES:
        if re.search(pattern, desc_lower, re.IGNORECASE):
            return label
    return None


def detect_labels(description: str) -> List[str]:
    """Todoist labels for a 0g goal: always #0g, plus the detected domain."""
    labels = [OG_LABEL]
    domain = detect_domain(description)
    if domain and domain not in labels:
        labels.append(domain)
    return labels


def route_goal(description: str) -> Dict:
    """{content, domain, labels, project_id}: everything a writer needs to file
    one 0g goal. Used by run_sync and by the /0g skill (`0g-sync.py route`)."""
    domain = detect_domain(description)
    return {"content": description, "domain": domain,
            "labels": detect_labels(description), "project_id": project_for(domain)}

# --- Logging ---

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("0g-sync")


# --- Data ---

class MdTask(NamedTuple):
    line_idx: int
    raw_line: str
    checked: bool
    description: str
    minutes: Optional[int]

    @property
    def key(self) -> str:
        return self.description.strip().lower()

    @property
    def todoist_content(self) -> str:
        if self.minutes:
            return f"{self.description} {{{self.minutes}}}"
        return self.description


# --- API Key ---

def get_api_key() -> str:
    """Read the token file, then TODOIST_API_KEY from env, then macOS Keychain."""
    f = Path.home() / ".config/todoist/token"   # single source (2026-11-08)
    if f.exists() and f.read_text().strip():
        return f.read_text().strip()
    key = os.environ.get("TODOIST_API_KEY")
    if key:
        return key
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "todoist-api-key", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    logger.error("No API key found. Set TODOIST_API_KEY or add to macOS Keychain:")
    logger.error("  security add-generic-password -s todoist-api-key -a mckay -w YOUR_KEY")
    sys.exit(1)


# --- Markdown Parsing ---

def parse_md(filepath: Path) -> Tuple[List[str], List[MdTask], List[MdTask], int, int]:
    """
    Parse the build order file.

    Returns (lines, og_tasks, later_tasks, og_heading_idx, later_heading_idx).
    Heading indices are -1 if section not found.
    """
    text = filepath.read_text(encoding="utf-8")
    lines = text.split("\n")

    og_start = -1
    later_start = -1
    og_tasks: List[MdTask] = []
    later_tasks: List[MdTask] = []

    # Find section boundaries
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") and OG_HEADING in stripped and "###" not in line[:4]:
            og_start = i
        elif stripped.startswith("### ") and LATER_HEADING in stripped:
            later_start = i

    if og_start < 0:
        logger.warning("Could not find ## 0₲ section in %s", filepath)
        return lines, [], [], -1, -1

    # Determine section end boundaries
    def section_end(start: int, heading_level: str) -> int:
        for j in range(start + 1, len(lines)):
            s = lines[j].strip()
            if s.startswith("## ") and heading_level == "##":
                return j
            if s.startswith("## ") and heading_level == "###":
                return j
            if s.startswith("### ") and heading_level == "###" and j != start:
                return j
        return len(lines)

    # Parse 0₲ tasks (between ## 0₲ and next heading)
    og_end = later_start if later_start > og_start else section_end(og_start, "##")
    for i in range(og_start + 1, og_end):
        task = _parse_task_line(i, lines[i])
        if task:
            og_tasks.append(task)

    # Parse 以后的目标 tasks
    if later_start >= 0:
        later_end = section_end(later_start, "###")
        for i in range(later_start + 1, later_end):
            task = _parse_task_line(i, lines[i])
            if task:
                later_tasks.append(task)

    return lines, og_tasks, later_tasks, og_start, later_start


def _parse_task_line(idx: int, line: str) -> Optional[MdTask]:
    m = TASK_RE.match(line)
    if not m:
        return None
    checked = m.group(2).lower() == "x"
    description = m.group(4).strip()
    minutes = None
    if m.group(5):
        minutes = int(m.group(5))
    elif m.group(6):
        minutes = int(m.group(6))
    return MdTask(idx, line, checked, description, minutes)


def write_md(filepath: Path, lines: List[str]):
    """Atomic write: write to tmp then rename."""
    content = "\n".join(lines)
    tmp = filepath.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(filepath)


# --- State ---

def load_state() -> Dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"version": 1, "tasks": {}}


def save_state(state: Dict):
    state["last_sync"] = datetime.now().astimezone().isoformat()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# --- Todoist Client ---

class TodoistClient:
    def __init__(self, api_token: str):
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

    def get_project_tasks(self, project_id: str) -> List[Dict]:
        return self._list_tasks({"project_id": project_id})

    def get_label_tasks(self, label: str) -> List[Dict]:
        """Active tasks carrying `label` in any project (API v1 `label` param;
        verified live 2026-11-09 — unlike a `@label` filter string, it is not
        confused by the leading '#'). How 0g goals are found now that they live
        in their domain projects."""
        return self._list_tasks({"label": label})

    def _list_tasks(self, base_params: Dict) -> List[Dict]:
        all_tasks = []
        cursor = None
        while True:
            params = dict(base_params, limit=200)
            if cursor:
                params["cursor"] = cursor
            resp = requests.get(
                f"{TODOIST_API_BASE}/tasks",
                headers=self.headers, params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            all_tasks.extend(data.get("results", []))
            cursor = data.get("next_cursor")
            if not cursor:
                break
        return all_tasks

    def create_task(self, content: str, minutes: Optional[int] = None,
                    labels: Optional[List[str]] = None,
                    project_id: Optional[str] = None) -> Dict:
        payload = {
            "content": content,
            "project_id": project_id or TODOIST_0G_PROJECT_ID,
            "priority": 4,  # API 4 = display p1
            "due_string": "today",
        }
        if minutes:
            payload["duration"] = minutes
            payload["duration_unit"] = "minute"
        if labels:
            payload["labels"] = labels
        resp = requests.post(
            f"{TODOIST_API_BASE}/tasks",
            headers=self.headers, json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    def update_task(self, task_id: str, updates: Dict):
        resp = requests.post(
            f"{TODOIST_API_BASE}/tasks/{task_id}",
            headers=self.headers, json=updates,
        )
        resp.raise_for_status()

    def delete_task(self, task_id: str):
        resp = requests.delete(
            f"{TODOIST_API_BASE}/tasks/{task_id}",
            headers=self.headers,
        )
        resp.raise_for_status()

    def get_completed_tasks(self, since: datetime, until: datetime,
                            project_id: Optional[str] = None) -> List[Dict]:
        """Return tasks completed in [since, until]. Optionally filter by project_id.
        Times must be timezone-aware ISO 8601."""
        all_tasks: List[Dict] = []
        cursor = None
        while True:
            params = {
                "since": since.isoformat(),
                "until": until.isoformat(),
                "limit": 200,
            }
            if project_id:
                params["project_id"] = project_id
            if cursor:
                params["cursor"] = cursor
            resp = requests.get(
                f"{TODOIST_API_BASE}/tasks/completed/by_completion_date",
                headers=self.headers, params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            all_tasks.extend(data.get("items") or data.get("results") or [])
            cursor = data.get("next_cursor")
            if not cursor:
                break
        return all_tasks


# --- Fuzzy Matching ---

# Annotation tokens that Todoist content may carry but a bare task.key won't:
# {N} / <N> / (N) and trailing @code labels. Strip before comparison.
ANNOTATION_RE = re.compile(r"[{<(]\d+[)>}]|@\S+")


def normalize_content(s: str) -> str:
    """Lowercase + strip annotation tokens so MD task.key and Todoist content
    can be compared apples-to-apples."""
    return ANNOTATION_RE.sub("", s).strip().lower()


def fuzzy_match(new_key: str, state_keys: set) -> Optional[str]:
    """Find a state key that shares 60%+ words with new_key."""
    new_words = set(new_key.split())
    if not new_words:
        return None
    best = None
    best_score = 0.0
    for sk in state_keys:
        sk_words = set(sk.split())
        if not sk_words:
            continue
        overlap = len(new_words & sk_words) / max(len(new_words), len(sk_words))
        if overlap > best_score and overlap >= 0.6:
            best_score = overlap
            best = sk
    return best


# --- Sync Mode ---

def find_neg1_section(lines: List[str]) -> Tuple[int, int]:
    """Return (start, end) line indices of the `## -1₲` section, end exclusive.
    Returns (-1, -1) if section is not found."""
    start = -1
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("## ") and NEG1_HEADING in s:
            start = i
            break
    if start < 0:
        return -1, -1
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip().startswith("## "):
            end = j
            break
    return start, end


def mark_neg1_completed(client: TodoistClient, lines: List[str],
                        dry_run: bool = False) -> List[int]:
    """Find unchecked -1₲ lines whose Todoist task was completed in the last
    24 hours; return their line indices for flipping to [x].

    Uses Todoist's by_completion_date API restricted to the 0g project.
    Matches Todoist completed-task content against MD line description by
    normalized-content equality / substring. Avoids false positives from
    lines that were never pushed to Todoist (e.g. user-typed bullets that
    /-1g hasn't synced) since those will not appear in completed-task feed.
    """
    start, end = find_neg1_section(lines)
    if start < 0:
        return []

    # Collect unchecked task lines in -1₲: (line_idx, normalized_description).
    md_lines: List[Tuple[int, str]] = []
    for i in range(start + 1, end):
        m = CHECKBOX_RE.match(lines[i])
        if not m or m.group(2).strip() != "":
            continue
        desc = m.group(4).strip()
        if not desc:
            continue
        md_lines.append((i, normalize_content(desc)))

    if not md_lines:
        return []

    # Query Todoist for tasks completed in the last 24h, scoped to the 0g project.
    now = datetime.now().astimezone()
    since = now - timedelta(hours=24)
    try:
        completed = client.get_completed_tasks(since, now,
                                               project_id=TODOIST_0G_PROJECT_ID)
    except requests.exceptions.HTTPError as e:
        logger.warning("Failed to fetch completed -1g tasks: %s", e)
        return []

    completed_norms = [normalize_content(t.get("content", "")) for t in completed]
    completed_norms = [c for c in completed_norms if c]

    flipped_indices = []
    for idx, norm_desc in md_lines:
        # Match by exact equality or bidirectional substring (handles {N}/(N) variation).
        matched = any(
            norm_desc == c or norm_desc in c or c in norm_desc
            for c in completed_norms
        )
        if not matched:
            continue
        if dry_run:
            logger.info("[DRY RUN] Would flip -1₲ line %d: %s", idx, lines[idx].strip())
        else:
            logger.info("-1₲ completed in Todoist, marking [x]: %s", lines[idx].strip())
        flipped_indices.append(idx)

    return flipped_indices


def run_sync(client: TodoistClient, dry_run: bool):
    logger.info("=== Sync mode ===")

    state = load_state()
    lines, og_tasks, _, og_start, _ = parse_md(MD_FILE)

    if og_start < 0:
        logger.error("No 0₲ section found. Aborting.")
        return

    unchecked = [t for t in og_tasks if not t.checked]
    logger.info("Found %d unchecked tasks in 0₲", len(unchecked))

    # Step 1: Get active 0g goals — by #0g label, since each goal now lives in
    # its domain's project (2026-11-09). A task that loses the label is treated
    # as gone, exactly as one moved out of the 0g project was before.
    todoist_tasks = client.get_label_tasks(OG_LABEL)
    todoist_active_ids = {t["id"] for t in todoist_tasks}
    logger.info("Todoist has %d active #0g goals", len(todoist_active_ids))

    # Step 2: Reverse sync — find completed/deleted Todoist tasks, flip [ ] to [x] in MD.
    # Preserving the line (rather than removing) lets the 03:59 build-order archive
    # capture the day's completed work in v_logs. Cleanup mode at 5:30 strips the
    # [x] lines from the live build order so they don't carry into the next day.
    lines_to_check = set()
    keys_to_remove = []

    for key, entry in state.get("tasks", {}).items():
        tid = entry["todoist_id"]
        if tid not in todoist_active_ids:
            for task in og_tasks:
                if task.key == key and not task.checked:
                    lines_to_check.add(task.line_idx)
                    logger.info("Completed in Todoist, marking [x] in MD: %s", task.description)
                    break
            keys_to_remove.append(key)

    for key in keys_to_remove:
        del state["tasks"][key]

    # Step 3: Forward sync — new MD tasks to Todoist
    state_keys = set(state.get("tasks", {}).keys())
    created = 0

    for task in unchecked:
        if task.line_idx in lines_to_check:
            continue

        # Exact match
        if task.key in state_keys:
            continue

        # Fuzzy match (user edited text)
        fmatch = fuzzy_match(task.key, state_keys)
        if fmatch:
            # Update state key and Todoist task content
            entry = state["tasks"].pop(fmatch)
            state["tasks"][task.key] = entry
            state_keys.discard(fmatch)
            state_keys.add(task.key)
            if not dry_run:
                try:
                    client.update_task(entry["todoist_id"], {"content": task.todoist_content})
                    logger.info("Updated (fuzzy match): %s -> %s", fmatch[:40], task.key[:40])
                except requests.exceptions.HTTPError as e:
                    logger.warning("Failed to update task: %s", e)
            else:
                logger.info("[DRY RUN] Would update: %s -> %s", fmatch[:40], task.key[:40])
            continue

        # Check if task already exists in Todoist (created by /0g or another source).
        # Normalize both sides — strip {N}/<N>/(N)/@code annotations — so a
        # Todoist task created with "Foo {30}" matches an MD desc "Foo".
        existing_match = None
        task_norm = normalize_content(task.key)
        task_words = set(task_norm.split())
        for tt in todoist_tasks:
            tt_norm = normalize_content(tt["content"])
            # First try: normalized exact match (most robust).
            if tt_norm and task_norm and (tt_norm == task_norm or
                                          task_norm in tt_norm or
                                          tt_norm in task_norm):
                existing_match = tt
                break
            # Fallback: word-set overlap >= 60%.
            tt_words = set(tt_norm.split())
            if task_words and tt_words:
                overlap = len(task_words & tt_words) / max(len(task_words), len(tt_words))
                if overlap >= 0.6:
                    existing_match = tt
                    break

        if existing_match:
            # Adopt the existing Todoist task into state instead of creating a duplicate
            state["tasks"][task.key] = {
                "todoist_id": existing_match["id"],
                "minutes": task.minutes,
            }
            state_keys.add(task.key)
            logger.info("Adopted existing Todoist task: %s (id=%s)", task.description, existing_match["id"])
            continue

        # New task: labels + project from the goal text (route_goal)
        route = route_goal(task.description)
        labels, project_id = route["labels"], route["project_id"]
        if dry_run:
            logger.info("[DRY RUN] Would create: %s (%sm) labels=%s project=%s",
                        task.description, task.minutes, labels, project_id)
        else:
            try:
                result = client.create_task(task.todoist_content, task.minutes,
                                            labels=labels, project_id=project_id)
                state["tasks"][task.key] = {
                    "todoist_id": result["id"],
                    "minutes": task.minutes,
                }
                state_keys.add(task.key)
                created += 1
                logger.info("Created in Todoist: %s (id=%s)", task.description, result["id"])
            except requests.exceptions.HTTPError as e:
                logger.error("Failed to create task '%s': %s", task.description, e)

    # Step 4: Orphan cleanup — state entries with no matching MD task
    current_keys = {t.key for t in unchecked if t.line_idx not in lines_to_check}
    orphaned = [k for k in state.get("tasks", {}) if k not in current_keys]
    for key in orphaned:
        entry = state["tasks"].pop(key)
        tid = entry["todoist_id"]
        if tid in todoist_active_ids:
            if dry_run:
                logger.info("[DRY RUN] Would delete orphaned Todoist task: %s", key[:50])
            else:
                try:
                    client.delete_task(tid)
                    logger.info("Deleted orphaned Todoist task: %s", key[:50])
                except requests.exceptions.HTTPError as e:
                    logger.warning("Failed to delete orphan %s: %s", tid, e)

    # Step 4b: Process -1₲ section — flip lines whose Todoist task is no longer active.
    neg1_flipped = mark_neg1_completed(client, lines, dry_run=dry_run)
    if neg1_flipped:
        for idx in neg1_flipped:
            lines_to_check.add(idx)

    # Step 5: Write MD if lines were flipped
    if lines_to_check and not dry_run:
        new_lines = list(lines)
        for idx in lines_to_check:
            new_lines[idx] = flip_to_checked(new_lines[idx])
        write_md(MD_FILE, new_lines)
        logger.info("Marked %d completed lines [x] in MD", len(lines_to_check))

    # Step 6: Save state
    if not dry_run:
        save_state(state)

    logger.info(
        "Sync done: %d created, %d marked [x], %d orphans cleaned",
        created, len(lines_to_check), len(orphaned),
    )


# --- Cleanup Mode ---

def run_cleanup(client: TodoistClient, dry_run: bool):
    logger.info("=== Cleanup mode (5:30am) ===")

    lines, og_tasks, _, og_start, later_start = parse_md(MD_FILE)

    if og_start < 0:
        logger.error("No 0₲ section found. Aborting.")
        return

    unchecked = [t for t in og_tasks if not t.checked]
    logger.info("Found %d unchecked tasks to move to 以后的目标", len(unchecked))

    if unchecked:
        # Collect lines to move (preserve original text)
        move_indices = sorted([t.line_idx for t in unchecked])
        moved_lines = [lines[i] for i in move_indices]

        if dry_run:
            for ml in moved_lines:
                logger.info("[DRY RUN] Would move: %s", ml.strip())
        else:
            # Remove from 0₲ (reverse order to preserve indices)
            for idx in reversed(move_indices):
                del lines[idx]

            # Re-find 以后的目标 heading after deletion
            insert_idx = None
            for i, line in enumerate(lines):
                if LATER_HEADING in line and line.strip().startswith("###"):
                    insert_idx = i
                    break

            if insert_idx is not None:
                for j, ml in enumerate(moved_lines):
                    lines.insert(insert_idx + 1 + j, ml)
            else:
                # Create the section if it doesn't exist — insert after 0₲ tasks
                # Find end of 0₲ section
                new_og_start = -1
                for i, line in enumerate(lines):
                    if line.strip().startswith("## ") and OG_HEADING in line:
                        new_og_start = i
                        break
                if new_og_start >= 0:
                    insert_at = new_og_start + 1
                    # Skip past any remaining lines in 0₲
                    while insert_at < len(lines):
                        s = lines[insert_at].strip()
                        if s.startswith("## ") or s.startswith("### "):
                            break
                        insert_at += 1
                    lines.insert(insert_at, "")
                    lines.insert(insert_at + 1, f"### {LATER_HEADING}")
                    for j, ml in enumerate(moved_lines):
                        lines.insert(insert_at + 2 + j, ml)

            lines = _collapse_blanks(lines)
            write_md(MD_FILE, lines)
            logger.info("Moved %d tasks to 以后的目标", len(moved_lines))

    # Also remove checked tasks from 0₲ (completed in MD)
    checked = [t for t in og_tasks if t.checked]
    if checked and not dry_run:
        # Re-read since we may have written above
        lines, og_tasks_new, _, _, _ = parse_md(MD_FILE)
        checked_indices = [t.line_idx for t in og_tasks_new if t.checked]
        if checked_indices:
            new_lines = [l for i, l in enumerate(lines) if i not in set(checked_indices)]
            new_lines = _collapse_blanks(new_lines)
            write_md(MD_FILE, new_lines)
            logger.info("Removed %d checked tasks from 0₲", len(checked_indices))

    # Clear yesterday's goals: everything still in the 0g project (leftover
    # -1g block goals) plus every #0g goal filed in a domain project.
    by_id: Dict[str, Dict] = {}
    for t in client.get_project_tasks(TODOIST_0G_PROJECT_ID) + client.get_label_tasks(OG_LABEL):
        by_id.setdefault(t["id"], t)
    todoist_tasks = list(by_id.values())
    if todoist_tasks:
        if dry_run:
            logger.info("[DRY RUN] Would delete %d Todoist tasks", len(todoist_tasks))
        else:
            for t in todoist_tasks:
                try:
                    client.delete_task(t["id"])
                except requests.exceptions.HTTPError as e:
                    logger.warning("Failed to delete task %s: %s", t["id"], e)
            logger.info("Deleted %d Todoist goal tasks (0g project ∪ #0g label)", len(todoist_tasks))

    # Reset state
    if not dry_run:
        save_state({"version": 1, "tasks": {}})
        logger.info("State reset")

    logger.info("Cleanup done")


# --- Helpers ---

def _collapse_blanks(lines: List[str]) -> List[str]:
    """Collapse runs of 3+ blank lines down to 2."""
    result = []
    blank_count = 0
    for line in lines:
        if line.strip() == "":
            blank_count += 1
            if blank_count <= 2:
                result.append(line)
        else:
            blank_count = 0
            result.append(line)
    return result


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Sync 0₲ goals between MD and Todoist")
    parser.add_argument("mode", choices=["sync", "cleanup", "route"], help="Operation mode")
    parser.add_argument("goals", nargs="*",
                        help="route: goal texts → one JSON line each {content, domain, labels, project_id}")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.mode == "route":
        # No API call: the /0g skill asks here for each goal's labels + project
        # so skill and cron can never disagree on where a goal is filed.
        for g in args.goals:
            print(json.dumps(route_goal(g), ensure_ascii=False))
        return

    api_token = get_api_key()
    client = TodoistClient(api_token)

    if args.mode == "sync":
        run_sync(client, dry_run=args.dry_run)
    elif args.mode == "cleanup":
        run_cleanup(client, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
