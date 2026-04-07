#!/usr/bin/env python3
"""Export Claude Code session transcripts (.jsonl) to readable markdown files.

Reads ~/.claude/projects/<project>/*.jsonl and writes .md files to
vault/i447/i446/ai-transcripts/<project>/. Idempotent: skips sessions
already exported unless --force is passed.

Large sessions (> SEGMENT_TURNS user turns) are split into multiple files
named <date>_<slug>_p01.md, <date>_<slug>_p02.md, etc.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
OUTPUT_BASE = Path.home() / "vault" / "i447" / "i446" / "ai-transcripts"

# Split sessions with more than this many user turns into multiple files
SEGMENT_TURNS = 10


def slugify(text: str, max_len: int = 60) -> str:
    if not text:
        return "untitled"
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    s = s.strip("-")
    return s[:max_len].rstrip("-")


def extract_text_from_content(content) -> str:
    """Extract readable text from a message content field (str or list)."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts = []
    has_tool_use = False
    for block in content:
        btype = block.get("type", "")
        if btype == "text":
            text = block.get("text", "").strip()
            if text:
                parts.append(text)
        elif btype == "thinking":
            pass  # always skip thinking blocks
        elif btype == "tool_use":
            has_tool_use = True
            # Omit params; just note the tool name
        elif btype == "tool_result":
            pass  # skip tool results in user messages
        elif btype == "tool_reference":
            pass  # skip

    result = "\n\n".join(parts)
    if has_tool_use:
        result = (result + "\n\n*(tool calls)*").strip()
    return result


def parse_session(path: Path) -> dict:
    """Parse a .jsonl session file into structured data."""
    session_id = path.stem
    slug = None
    date_str = None
    user_turns = []
    assistant_turns = []
    pairs = []  # list of (user_text, assistant_text)

    entries = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    # Extract slug and date from first user entry
    for entry in entries:
        if entry.get("type") == "user" and not slug:
            slug = entry.get("slug", "")
            # Try to get date from message timestamp or fall back to file mtime
            # No explicit timestamp in JSONL — use file mtime for date
            mtime = path.stat().st_mtime
            date_str = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d")
            break

    if not slug:
        slug = "untitled"
    if not date_str:
        mtime = path.stat().st_mtime
        date_str = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d")

    # Build conversation pairs: group all assistant entries between two user entries
    # as a single turn. Multiple tool-call/response cycles collapse into one turn.
    current_user = None
    asst_parts = []  # accumulate assistant text blocks for current turn

    def flush_pair():
        nonlocal current_user, asst_parts
        asst_text = "\n\n".join(p for p in asst_parts if p).strip()
        if current_user or asst_text:
            pairs.append((current_user or "", asst_text))
        current_user = None
        asst_parts = []

    for entry in entries:
        etype = entry.get("type")
        if etype == "user":
            msg = entry.get("message", {})
            content = msg.get("content", "")
            text = extract_text_from_content(content)
            if text:
                # New real user message — flush previous pair first
                flush_pair()
                current_user = text
            # else: pure tool-result message, ignore
        elif etype == "assistant":
            msg = entry.get("message", {})
            content = msg.get("content", [])
            text = extract_text_from_content(content)
            if text:
                asst_parts.append(text)

    flush_pair()  # flush last pair

    return {
        "session_id": session_id,
        "slug": slug,
        "date": date_str,
        "pairs": pairs,
    }


def render_segment(session: dict, pairs: list, part, total_parts) -> str:
    lines = []

    slug = session["slug"]
    date = session["date"]
    session_id = session["session_id"]
    part_label = f" (part {part}/{total_parts})" if part is not None else ""
    title = f"{slug}{part_label}"

    lines.append("---")
    lines.append(f"session_id: {session_id}")
    lines.append(f"slug: \"{slug}\"")
    lines.append(f"date: {date}")
    if part is not None:
        lines.append(f"part: {part}")
        lines.append(f"total_parts: {total_parts}")
    lines.append(f"turns: {len(pairs)}")
    lines.append("type: ai-transcript")
    lines.append("tags: [i446, ai-transcript]")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")

    for i, (user_text, asst_text) in enumerate(pairs, 1):
        lines.append(f"## Turn {i}")
        lines.append("")
        if user_text:
            lines.append("**User:**")
            lines.append("")
            lines.append(user_text)
            lines.append("")
        if asst_text:
            lines.append("**Assistant:**")
            lines.append("")
            lines.append(asst_text)
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def output_paths_for_session(session: dict, out_dir: Path, segment_turns: int):
    """Return the list of output paths that would be written for this session."""
    pairs = session["pairs"]
    date = session["date"]
    slug = slugify(session["slug"])

    if len(pairs) <= segment_turns:
        return [out_dir / f"{date}_{slug}.md"]

    # Split into segments
    segments = [pairs[i:i + segment_turns] for i in range(0, len(pairs), segment_turns)]
    total = len(segments)
    return [out_dir / f"{date}_{slug}_p{i+1:02d}.md" for i in range(total)]


def export_session(session: dict, out_dir: Path, segment_turns: int, force: bool, verbose: bool) -> int:
    """Write .md files for a session. Returns number of files written."""
    pairs = session["pairs"]
    date = session["date"]
    slug = slugify(session["slug"])

    if not pairs:
        if verbose:
            print(f"  skip (no turns):   {session['session_id'][:8]}…")
        return 0

    paths = output_paths_for_session(session, out_dir, segment_turns)
    all_exist = all(p.exists() for p in paths)
    if all_exist and not force:
        if verbose:
            print(f"  skip (exists):     {paths[0].name}")
        return 0

    if len(pairs) <= segment_turns:
        md = render_segment(session, pairs, part=None, total_parts=None)
        out_dir.mkdir(parents=True, exist_ok=True)
        paths[0].write_text(md, encoding="utf-8")
        if verbose:
            print(f"  exported:          {paths[0].name}")
        return 1
    else:
        segments = [pairs[i:i + segment_turns] for i in range(0, len(pairs), segment_turns)]
        total = len(segments)
        written = 0
        for i, seg_pairs in enumerate(segments):
            p = paths[i]
            if p.exists() and not force:
                if verbose:
                    print(f"  skip (exists):     {p.name}")
                continue
            md = render_segment(session, seg_pairs, part=i + 1, total_parts=total)
            out_dir.mkdir(parents=True, exist_ok=True)
            p.write_text(md, encoding="utf-8")
            if verbose:
                print(f"  exported:          {p.name}")
            written += 1
        return written


def export_project(project_dir: Path, force: bool = False, verbose: bool = False) -> tuple[int, int]:
    """Export all sessions in a project directory. Returns (exported, skipped)."""
    project_name = project_dir.name
    out_dir = OUTPUT_BASE / project_name

    jsonl_files = sorted(project_dir.glob("*.jsonl"))
    exported = 0
    skipped = 0

    for jsonl_path in jsonl_files:
        session = parse_session(jsonl_path)
        if not session["pairs"]:
            skipped += 1
            continue

        paths = output_paths_for_session(session, out_dir, SEGMENT_TURNS)
        all_exist = all(p.exists() for p in paths)
        if all_exist and not force:
            skipped += 1
            if verbose:
                print(f"  skip (exists):     {paths[0].name}")
            continue

        n = export_session(session, out_dir, SEGMENT_TURNS, force=force, verbose=verbose)
        if n > 0:
            exported += n
        else:
            skipped += 1

    return exported, skipped


def main():
    parser = argparse.ArgumentParser(description="Export Claude Code transcripts to markdown.")
    parser.add_argument("--force", action="store_true", help="Re-export even if file exists")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show per-session details")
    parser.add_argument(
        "--session", metavar="SESSION_ID",
        help="Export only a specific session ID (partial match OK)"
    )
    parser.add_argument(
        "--project", metavar="PROJECT",
        help="Export only a specific project directory name"
    )
    args = parser.parse_args()

    if not PROJECTS_DIR.exists():
        print(f"Error: {PROJECTS_DIR} not found", file=sys.stderr)
        sys.exit(1)

    total_exported = 0
    total_skipped = 0

    project_dirs = [d for d in sorted(PROJECTS_DIR.iterdir()) if d.is_dir()]
    if args.project:
        project_dirs = [d for d in project_dirs if args.project in d.name]

    for proj_dir in project_dirs:
        if args.verbose:
            print(f"\nProject: {proj_dir.name}")

        if args.session:
            # Export specific session
            matches = list(proj_dir.glob(f"*{args.session}*.jsonl"))
            if not matches:
                continue
            for jsonl_path in matches:
                session = parse_session(jsonl_path)
                out_dir = OUTPUT_BASE / proj_dir.name
                n = export_session(session, out_dir, SEGMENT_TURNS, args.force, args.verbose)
                total_exported += n
        else:
            exp, skip = export_project(proj_dir, force=args.force, verbose=args.verbose)
            total_exported += exp
            total_skipped += skip

    print(f"Exported {total_exported} file(s), skipped {total_skipped} session(s).")


if __name__ == "__main__":
    main()
