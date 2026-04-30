#!/usr/bin/env python3
"""build-order-enrich.py -- Populate build order with meetings, completed tasks, and time entries.

Runs every 2h (aligned with 地支 blocks). For each past block today, populates:
- **Meetings**: calendar events + d357 links
- **Completed**: habits/tasks done (from completed-today.json)
- **Time**: Toggl entries

Idempotent: sections are replaced on each run, not appended.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
BUILD_ORDER = Path.home() / "vault/g245/-1₦ , 0₦ - Neon {Build Order}.md"
COMPLETED_TODAY = Path.home() / "vault/z_ibx/completed-today.json"
COMPLETED_ARCHIVE_DIR = Path.home() / "vault/z_ibx/completed-archive"
D357_DIR = Path.home() / "vault/d357"
TOGGL_CLI = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"

BLOCKS = [
    ("卯", 4, 5),
    ("辰", 6, 7),
    ("巳", 8, 9),
    ("午", 10, 11),
    ("未", 12, 13),
    ("申", 14, 15),
    ("酉", 16, 17),
    ("戌", 18, 19),
    ("亥", 20, 21),
]

# Markers to strip when parsing block names
MARKERS = ["☀️", "📧", "⏰"]


def get_current_block_idx():
    now = datetime.now(TZ)
    return max(0, min(8, (now.hour - 4) // 2))


def block_name_clean(line):
    name = line.strip().lstrip("- ").strip()
    for m in MARKERS:
        name = name.replace(m, "").strip()
    # Strip duration suffix like (134min) or (20分, 134min)
    name = re.sub(r"\s*\([^)]*(?:分|min)[^)]*\)\s*$", "", name)
    return name


def get_toggl_today():
    """Fetch today's Toggl entries."""
    try:
        r = subprocess.run(
            ["python3", str(TOGGL_CLI), "today"],
            capture_output=True, text=True, timeout=15,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def parse_toggl_entries(raw):
    """Parse Toggl output into [(start_hh_mm, end_hh_mm, description, project), ...]."""
    entries = []
    for line in raw.split("\n"):
        line = line.strip()
        m = re.match(r"(\d{2}:\d{2})-(\d{2}:\d{2}|running)\s+(.+?)(?:\s+@(\S+))?\s+\(", line)
        if m:
            start, end, desc, proj = m.group(1), m.group(2), m.group(3).strip(), m.group(4) or ""
            entries.append((start, end, desc, proj))
    return entries


def _archive_completed(date_str, names, points=None, timestamps=None):
    """Persist completed tasks to a date-keyed archive file.
    Merges with any existing archive for the same date."""
    COMPLETED_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_file = COMPLETED_ARCHIVE_DIR / f"{date_str}.json"
    existing_names = []
    existing_points = {}
    existing_timestamps = {}
    if archive_file.exists():
        try:
            raw = json.loads(archive_file.read_text())
            if isinstance(raw, list):
                existing_names = raw  # old format
            elif isinstance(raw, dict):
                existing_names = raw.get("names", [])
                existing_points = raw.get("points", {})
                existing_timestamps = raw.get("timestamps", {})
        except Exception:
            pass
    merged_names = list(dict.fromkeys(existing_names + names))
    merged_points = {**existing_points, **(points or {})}
    merged_timestamps = {**existing_timestamps, **(timestamps or {})}
    archive_file.write_text(json.dumps({
        "names": merged_names, "points": merged_points,
        "timestamps": merged_timestamps,
    }))


def get_completed_today():
    """Read completed tasks for today.
    Reads from completed-today.json (live) and merges with the archive.
    Returns (names, points_dict, timestamps_dict)."""
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    names = []
    points = {}
    timestamps = {}

    # Live file
    if COMPLETED_TODAY.exists():
        try:
            data = json.loads(COMPLETED_TODAY.read_text())
            if data.get("date") == today:
                names = data.get("names", [])
                points = data.get("points", {})
                timestamps = data.get("timestamps", {})
        except Exception:
            pass

    # Archive (may have earlier entries from previous cron runs)
    archive_file = COMPLETED_ARCHIVE_DIR / f"{today}.json"
    if archive_file.exists():
        try:
            archived = json.loads(archive_file.read_text())
            if isinstance(archived, list):
                # Old format: just names
                names = list(dict.fromkeys(names + archived))
            elif isinstance(archived, dict):
                # New format: {names, points, timestamps}
                names = list(dict.fromkeys(names + archived.get("names", [])))
                for k, v in archived.get("points", {}).items():
                    if k not in points:
                        points[k] = v
                for k, v in archived.get("timestamps", {}).items():
                    if k not in timestamps:
                        timestamps[k] = v
        except Exception:
            pass

    # Persist to archive so tasks survive the daily reset
    if names:
        _archive_completed(today, names, points, timestamps)

    return names, points, timestamps


def get_d357_docs_today():
    """Find d357 docs filed today, return [(slug, title, hour), ...].

    Hour is extracted from the matching WAV file in recordings/ (which has HHMM
    in the filename), or from file mtime as fallback.
    """
    now = datetime.now(TZ)
    today_hyphen = now.strftime("%Y-%m-%d")
    today_dot = now.strftime("%Y.%m.%d")
    recordings_dir = Path.home() / "vault/h335/i9/recordings"
    docs = []
    for f in list(D357_DIR.glob(f"{today_hyphen}*.md")) + list(D357_DIR.glob(f"{today_dot}*.md")):
        text = f.read_text()
        title_match = re.search(r'^title:\s*"(.+?)"', text, re.MULTILINE)
        title = title_match.group(1) if title_match else f.stem
        slug = f.stem

        # Try to find hour from matching WAV filename
        hour = None
        # WAV files have format: YYYY-MM-DD-HHMM-name.wav
        for wav in recordings_dir.glob(f"{today_hyphen}-*.wav"):
            wav_m = re.match(r"\d{4}-\d{2}-\d{2}-(\d{2})(\d{2})-", wav.name)
            if wav_m:
                # Check if WAV name overlaps with doc slug
                wav_name_part = wav.stem.split("-", 3)[-1] if len(wav.stem.split("-")) > 3 else ""
                # Fuzzy: check if any words overlap (normalize colons and dots)
                wav_words = set(re.sub(r'[:.]+', '-', wav_name_part.lower()).split("-"))
                slug_name_clean = re.sub(r'^(\d{4}[\.-]\d{2}[\.-]\d{2})-?', '', slug)
                slug_words = set(re.sub(r'[:.]+', '-', slug_name_clean.lower()).split("-"))
                if wav_words & slug_words:
                    hour = int(wav_m.group(1))
                    break

        if hour is None:
            # Fallback 2: match against Toggl entries by title similarity
            toggl_raw = get_toggl_today()
            toggl_entries = parse_toggl_entries(toggl_raw)
            title_words = set(re.sub(r'[^a-z0-9\s]', '', title.lower()).split())
            for t_start, t_end, t_desc, t_proj in toggl_entries:
                t_words = set(re.sub(r'[^a-z0-9\s]', '', t_desc.lower()).split())
                if title_words & t_words and len(title_words & t_words) >= 1:
                    hour = int(t_start.split(":")[0])
                    break

        if hour is None:
            # Fallback 3: use file mtime (least accurate)
            mtime = datetime.fromtimestamp(f.stat().st_mtime, TZ)
            hour = mtime.hour

        docs.append((slug, title, hour))

    # Deduplicate by slug (both date formats may match the same doc)
    seen = set()
    unique = []
    for slug, title, hour in docs:
        if slug not in seen:
            seen.add(slug)
            unique.append((slug, title, hour))
    return unique


def timestamp_to_block_idx(hhmm):
    """Map an HH:MM timestamp to a block index (0-8). Returns None if unparseable."""
    if not hhmm:
        return None
    try:
        hour = int(hhmm.split(":")[0])
        return max(0, min(8, (hour - 4) // 2))
    except (ValueError, IndexError):
        return None


def time_to_minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def entries_in_block(entries, block_start_hour, block_end_hour):
    """Filter Toggl entries that overlap a block. Excludes 睡觉."""
    block_s = block_start_hour * 60
    block_e = (block_end_hour + 1) * 60  # inclusive end
    result = []
    for start, end, desc, proj in entries:
        if proj == "睡觉" or desc == "睡觉":
            continue  # sleep excluded from block view
        s_min = time_to_minutes(start)
        if end == "running":
            e_min = datetime.now(TZ).hour * 60 + datetime.now(TZ).minute
        else:
            e_min = time_to_minutes(end)
        if e_min > block_s and s_min < block_e:
            result.append((start, end, desc, proj))
    return result


def docs_in_block(docs, block_start_hour, block_end_hour):
    """Match d357 docs to a block by their hour."""
    result = []
    for slug, title, hour in docs:
        if block_start_hour <= hour <= block_end_hour:
            result.append((slug, title))
    return result


def _task_matches_entry(task_name, entry_desc):
    """Check if a completed task name matches a Toggl entry description."""
    t = set(re.sub(r'[^a-z0-9\s]', '', task_name.lower()).split())
    e = set(re.sub(r'[^a-z0-9\s]', '', entry_desc.lower()).split())
    if not t or not e:
        return False
    # At least one meaningful word overlaps
    overlap = t & e - {"the", "a", "an", "to", "of", "in", "for", "and", "or"}
    return len(overlap) >= 1


def completed_in_block(completed, toggl_entries, block_start_hour, block_end_hour):
    """Split completed tasks into (matched_to_time, unmatched).

    matched_to_time: set of task names that match a Toggl entry in this block.
    unmatched: list of task names with no matching time entry.
    """
    block_entries = entries_in_block(toggl_entries, block_start_hour, block_end_hour)
    matched = set()
    for task in completed:
        for _, _, desc, _ in block_entries:
            if _task_matches_entry(task, desc):
                matched.add(task)
                break
    # Unmatched: tasks not matched to any block's time entries
    # (caller handles dedup across blocks)
    return matched


def _block_total_minutes(block_entries):
    """Sum minutes across block entries, clamped to block boundaries."""
    total = 0
    for start, end, desc, proj in block_entries:
        if end == "running":
            now = datetime.now(TZ)
            e_min = now.hour * 60 + now.minute
        else:
            e_min = time_to_minutes(end)
        s_min = time_to_minutes(start)
        total += max(0, e_min - s_min)
    return total


def build_enrichment_sections(block_idx, toggl_entries, completed, d357_docs,
                              completed_claimed, points_map=None):
    """Build the enrichment text for a block.

    completed_claimed: set, mutated -- tracks which completed tasks have been
    claimed by a block so they aren't duplicated.
    points_map: dict mapping task name -> 分 value.
    Returns (sections, total_minutes, total_points).
    """
    points_map = points_map or {}
    block_name, start_h, end_h = BLOCKS[block_idx]

    block_entries = entries_in_block(toggl_entries, start_h, end_h)
    block_docs = docs_in_block(d357_docs, start_h, end_h)
    block_matched = completed_in_block(completed, toggl_entries, start_h, end_h)

    sections = []

    # Build a lookup: match d357 docs to time entries by word overlap
    doc_by_entry = {}  # index into block_entries -> (slug, title)
    claimed_docs = set()
    for ei, (start, end, desc, proj) in enumerate(block_entries):
        desc_words = set(re.sub(r'[^a-z0-9\s]', '', desc.lower()).split())
        for slug, title in block_docs:
            if slug in claimed_docs:
                continue
            title_words = set(re.sub(r'[^a-z0-9\s]', '', title.lower()).split())
            overlap = desc_words & title_words - {"the", "a", "an", "to", "of", "in", "for", "and", "or", "1"}
            if overlap:
                doc_by_entry[ei] = (slug, title)
                claimed_docs.add(slug)
                break

    # Unmatched d357 docs (no corresponding time entry)
    unmatched_docs = [(s, t) for s, t in block_docs if s not in claimed_docs]

    # Time entries (with d357 link inline + ✓ if a completed task matches)
    if block_entries:
        lines = []
        for ei, (start, end, desc, proj) in enumerate(block_entries):
            proj_str = f" @{proj}" if proj else ""
            check = ""
            for task in block_matched:
                if _task_matches_entry(task, desc) and task not in completed_claimed:
                    check = " ✓"
                    completed_claimed.add(task)
                    break
            d357_link = ""
            if ei in doc_by_entry:
                slug, title = doc_by_entry[ei]
                d357_link = f" [[d357/{slug}|d357]]"
            lines.append(f"    - {start}-{end} {desc}{proj_str}{d357_link}{check}")
        sections.append("    **Time**")
        sections.extend(lines)

    # Unmatched meetings (no time entry found)
    if unmatched_docs:
        for slug, title in unmatched_docs:
            sections.append(f"    - [[d357/{slug}|{title}]]")

    # Other tasks: completed tasks in this block's time window that don't
    # match any time entry AND haven't been claimed by another block
    unclaimed_in_block = []
    for task in completed:
        if task in completed_claimed:
            continue
        # Check if this task was likely done during this block
        # (heuristic: match against any time entry in this block by project)
        for _, _, _, proj in block_entries:
            if _task_matches_entry(task, proj) or _task_matches_entry(task, ""):
                unclaimed_in_block.append(task)
                completed_claimed.add(task)
                break

    total_min = _block_total_minutes(block_entries)
    # Sum 分 for tasks claimed by this block (matched + unclaimed_in_block)
    block_tasks = block_matched | set(unclaimed_in_block)
    total_pts = sum(points_map.get(t, 0) for t in block_tasks)
    return sections, total_min, total_pts


def enrich_build_order():
    if not BUILD_ORDER.exists():
        print("Build order not found")
        return

    text = BUILD_ORDER.read_text()
    if "## -1₲" not in text:
        print("No -1₲ section found")
        return

    # Gather data
    toggl_raw = get_toggl_today()
    toggl_entries = parse_toggl_entries(toggl_raw)
    completed, points_map, timestamps = get_completed_today()
    d357_docs = get_d357_docs_today()
    current_idx = get_current_block_idx()

    # Pre-bucket completed tasks by block using timestamps.
    # Tasks without timestamps fall back to the old heuristic (last past block).
    completed_by_block = {}  # block_idx -> [task_name, ...]
    no_timestamp = []
    for task in completed:
        ts = timestamps.get(task)
        bidx = timestamp_to_block_idx(ts) if ts else None
        if bidx is not None and bidx < current_idx:
            completed_by_block.setdefault(bidx, []).append(task)
        else:
            no_timestamp.append(task)

    completed_claimed = set()  # tracks which completed tasks are placed
    last_past_block_insert = None  # index in new_lines to insert "Other Tasks"

    lines = text.split("\n")
    new_lines = []
    in_section = False
    current_block_idx = None

    i = 0
    while i < len(lines):
        line = lines[i]

        # Detect section start
        if line.strip() == "## -1₲":
            in_section = True
            new_lines.append(line)
            i += 1
            continue

        # Detect section end
        if in_section and line.startswith("## ") and not line.startswith("## -1₲"):
            in_section = False
            new_lines.append(line)
            i += 1
            continue

        if not in_section:
            new_lines.append(line)
            i += 1
            continue

        # Inside -1₲ section
        # Check if this is a block header
        if line.startswith("- ") and not line.startswith("    "):
            block_name = block_name_clean(line)
            current_block_idx = None
            for idx, (bn, sh, eh) in enumerate(BLOCKS):
                if bn == block_name:
                    current_block_idx = idx
                    break

            # Strip old annotations (分, min) from header before re-adding
            clean_header = re.sub(r'\s*\([^)]*(?:分|min)[^)]*\)\s*$', '', line)

            # Collect all indented content under this block until the next
            # block header (or end of section). Separate into:
            # - goal_lines: checkbox lines (- [ ] or - [x])
            # - other: everything else (old enrichment, stray text)
            i += 1
            goal_lines = []
            while i < len(lines):
                l = lines[i]
                # Stop at next block header or section header
                if l.startswith("- ") and not l.startswith("    "):
                    break
                if l.startswith("## "):
                    break
                # Checkbox lines are goals (keep them)
                if re.match(r"^    - \[[ xX]\]", l):
                    goal_lines.append(l)
                # Everything else under this block is old enrichment (discard)
                # This includes **Meetings**, **Time**, meeting links, time entries
                i += 1

            # Add enrichment for past blocks only
            if current_block_idx is not None and current_block_idx < current_idx:
                prev_claimed = set(completed_claimed)
                enrichment, total_min, total_pts = build_enrichment_sections(
                    current_block_idx, toggl_entries, completed, d357_docs,
                    completed_claimed, points_map=points_map,
                )
                # Append 分 and/or minutes to header line
                annotations = []
                if total_pts > 0:
                    annotations.append(f"{total_pts}分")
                if total_min > 0:
                    annotations.append(f"{total_min}min")
                if annotations:
                    new_lines.append(f"{clean_header} ({', '.join(annotations)})")
                else:
                    new_lines.append(clean_header)
                # Write goal lines
                for gl in goal_lines:
                    new_lines.append(gl)
                for el in enrichment:
                    new_lines.append(el)
                # Per-block "Other Tasks": timestamp-bucketed tasks for this block
                block_other = [
                    t for t in completed_by_block.get(current_block_idx, [])
                    if t not in completed_claimed
                ]
                if block_other:
                    new_lines.append("    **Other Tasks**")
                    for t in block_other:
                        new_lines.append(f"    - {t} ✓")
                        completed_claimed.add(t)
                last_past_block_insert = len(new_lines)
            else:
                new_lines.append(clean_header)
                for gl in goal_lines:
                    new_lines.append(gl)

            continue

        # Regular line inside section (not a block header)
        new_lines.append(line)
        i += 1

    # Fallback: tasks without timestamps (old data) go to the last past block
    unclaimed = [t for t in no_timestamp if t not in completed_claimed]
    if unclaimed and last_past_block_insert is not None:
        # Check if this block already has an **Other Tasks** header
        already_has_header = any(
            new_lines[j].strip() == "**Other Tasks**"
            for j in range(max(0, last_past_block_insert - 40), last_past_block_insert)
        )
        other_lines = []
        if not already_has_header:
            other_lines.append("    **Other Tasks**")
        for t in unclaimed:
            other_lines.append(f"    - {t} ✓")
        for idx_offset, ol in enumerate(other_lines):
            new_lines.insert(last_past_block_insert + idx_offset, ol)

    BUILD_ORDER.write_text("\n".join(new_lines))
    n_claimed = len(completed_claimed)
    n_unclaimed = len(unclaimed)
    print(f"Enriched build order: {len(d357_docs)} meetings, {len(toggl_entries)} time entries, {n_claimed} tasks matched, {n_unclaimed} other tasks")


if __name__ == "__main__":
    enrich_build_order()
