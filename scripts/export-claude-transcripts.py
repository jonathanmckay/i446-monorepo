#!/usr/bin/env python3
"""Export Claude Code session transcripts (.jsonl) to readable markdown files.

Reads ~/.claude/projects/<project>/*.jsonl and writes .md files to
vault/i447/i446/.ai-transcripts/<project>/. Idempotent: skips sessions
already exported unless --force is passed.

Files are named <date>_<slug>_<sid8>.md, where sid8 is the first 8 chars of
the session id. Large sessions (> SEGMENT_TURNS user turns) are split into
<date>_<slug>_<sid8>_p01.md, _p02.md, etc.

Regression (2026-10-27): files used to be <date>_<slug>[_pNN].md. Claude Code
stopped writing a `slug` field (~Mar/Apr 2026), so every session became
"untitled" and same-day sessions overwrote each other (Straylight 10-26: 14
sessions, 2 files). A legacy-named file is still updated in place when its
frontmatter session_id is this session's, so existing vault files keep their
names; everything else gets the session-id suffix.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
PROJECTS_DIR_IX = Path.home() / ".claude" / "projects-ix"
OUTPUT_BASE = Path.home() / "vault" / "i447" / "i446" / ".ai-transcripts"

# Host-aware roots. We can't rely on hostname (ix is "Jonathans-Mac-mini").
# Instead: the straylight box is the only one with a mirrored ix tree at
# PROJECTS_DIR_IX. Any other box exports its own data into the ix/ subdir.
if PROJECTS_DIR_IX.exists():
    # Straylight — own data direct, mirrored ix data into ix/
    ROOTS = [
        ("straylight", PROJECTS_DIR, OUTPUT_BASE),
        ("ix",         PROJECTS_DIR_IX, OUTPUT_BASE / "ix"),
    ]
else:
    # ix (or any other leaf) — own data into ix/
    ROOTS = [
        ("ix", PROJECTS_DIR, OUTPUT_BASE / "ix"),
    ]

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

    # Extract slug + stable date from first user entry's timestamp field.
    # Fall back to file mtime only if no usable timestamp exists.
    for entry in entries:
        if entry.get("type") == "user" and not slug:
            slug = entry.get("slug", "")
            ts = entry.get("timestamp")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    date_str = dt.strftime("%Y-%m-%d")
                except (ValueError, AttributeError):
                    pass
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
        "source_path": path,
        "source_mtime": path.stat().st_mtime,
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


def _legacy_paths(date: str, slug: str, n_segments: int, out_dir: Path):
    if n_segments <= 1:
        return [out_dir / f"{date}_{slug}.md"]
    return [out_dir / f"{date}_{slug}_p{i+1:02d}.md" for i in range(n_segments)]


def _file_session_id(path: Path):
    """session_id from an exported file's frontmatter, or None."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(512)
    except OSError:
        return None
    m = re.search(r"^session_id:\s*(\S+)", head, re.M)
    return m.group(1) if m else None


def output_paths_for_session(session: dict, out_dir: Path, segment_turns: int):
    """Return the list of output paths that would be written for this session."""
    pairs = session["pairs"]
    date = session["date"]
    slug = slugify(session["slug"])
    sid = session["session_id"]
    n_segments = max(1, -(-len(pairs) // segment_turns))

    # Backward compat: keep updating a legacy-named file this session owns.
    # Every existing legacy target must be ours, or we'd clobber a collided
    # neighbour's shard.
    probes = (out_dir / f"{date}_{slug}.md", out_dir / f"{date}_{slug}_p01.md")
    if any(p.exists() and _file_session_id(p) == sid for p in probes):
        legacy = _legacy_paths(date, slug, n_segments, out_dir)
        if all((not p.exists()) or _file_session_id(p) == sid for p in legacy):
            return legacy

    base = f"{date}_{slug}_{sid[:8]}"
    if n_segments == 1:
        return [out_dir / f"{base}.md"]
    return [out_dir / f"{base}_p{i+1:02d}.md" for i in range(n_segments)]


def export_session(session: dict, out_dir: Path, segment_turns: int, force: bool, verbose: bool,
                   dry_run: bool = False) -> int:
    """Write .md files for a session. Returns number of files written."""
    pairs = session["pairs"]
    date = session["date"]
    slug = slugify(session["slug"])

    if not pairs:
        if verbose:
            print(f"  skip (no turns):   {session['session_id'][:8]}…")
        return 0

    paths = output_paths_for_session(session, out_dir, segment_turns)
    src_mtime = session.get("source_mtime", 0)
    # Re-export when source jsonl is newer than the existing md (live session
    # has new turns). Without this the exporter snapshots stop at first run.
    needs_refresh = any(
        (not p.exists()) or p.stat().st_mtime < src_mtime
        for p in paths
    )
    if not needs_refresh and not force:
        if verbose:
            print(f"  skip (current):    {paths[0].name}")
        return 0

    if dry_run:
        todo = [p for p in paths if force or (not p.exists()) or p.stat().st_mtime < src_mtime]
        for p in todo:
            print(f"  would write:       {out_dir.name}/{p.name}")
        return len(todo)

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
            if p.exists() and p.stat().st_mtime >= src_mtime and not force:
                if verbose:
                    print(f"  skip (current):    {p.name}")
                continue
            md = render_segment(session, seg_pairs, part=i + 1, total_parts=total)
            out_dir.mkdir(parents=True, exist_ok=True)
            p.write_text(md, encoding="utf-8")
            if verbose:
                print(f"  exported:          {p.name}")
            written += 1
        return written


def export_project(project_dir: Path, out_base: Path, sessions_to_export: list,
                   force: bool = False, verbose: bool = False,
                   dry_run: bool = False) -> tuple[int, int]:
    """Export the given pre-selected sessions in this project dir."""
    out_dir = out_base / project_dir.name
    exported = 0
    skipped = 0
    for session in sessions_to_export:
        n = export_session(session, out_dir, SEGMENT_TURNS, force=force, verbose=verbose,
                           dry_run=dry_run)
        if n > 0:
            exported += n
        else:
            skipped += 1
    return exported, skipped


def discover_sessions():
    """Scan all ROOTS, return a map of session_id -> (host, root, project_dir, session).
    For duplicates (same session_id present in multiple roots), keep the one with
    the most user turns (canonical = most-complete copy)."""
    chosen: dict[str, tuple[str, Path, Path, dict]] = {}
    for host, root, out_base in ROOTS:
        if not root.exists():
            continue
        for proj_dir in sorted(root.iterdir()):
            if not proj_dir.is_dir():
                continue
            for jsonl_path in sorted(proj_dir.glob("*.jsonl")):
                session = parse_session(jsonl_path)
                if not session["pairs"]:
                    continue
                sid = session["session_id"]
                nturns = len(session["pairs"])
                prior = chosen.get(sid)
                if prior is None or nturns > len(prior[3]["pairs"]):
                    chosen[sid] = (host, root, proj_dir, session)
    return chosen


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
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="Only sessions whose date is on/after this date")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the files that would be written; write nothing")
    args = parser.parse_args()

    chosen = discover_sessions()

    # Apply filters
    if args.session:
        chosen = {sid: v for sid, v in chosen.items() if args.session in sid}
    if args.project:
        chosen = {sid: v for sid, v in chosen.items() if args.project in v[2].name}
    if args.since:
        chosen = {sid: v for sid, v in chosen.items() if v[3]["date"] >= args.since}

    # Group by (host, project_dir) for export
    out_by_host: dict[tuple[str, Path, Path], list] = {}
    out_base_for_host = {host: out_base for host, _, out_base in ROOTS}
    for sid, (host, root, proj_dir, session) in chosen.items():
        key = (host, proj_dir, out_base_for_host[host])
        out_by_host.setdefault(key, []).append(session)

    total_exported = 0
    total_skipped = 0
    for (host, proj_dir, out_base), sessions in sorted(out_by_host.items(), key=lambda x: (x[0][0], x[0][1].name)):
        if args.verbose:
            print(f"\n[{host}] Project: {proj_dir.name}")
        exp, skip = export_project(proj_dir, out_base, sessions,
                                   force=args.force, verbose=args.verbose,
                                   dry_run=args.dry_run)
        total_exported += exp
        total_skipped += skip

    verb = "Would export" if args.dry_run else "Exported"
    print(f"{verb} {total_exported} file(s), skipped {total_skipped} session(s).")


if __name__ == "__main__":
    main()
