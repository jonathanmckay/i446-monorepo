#!/usr/bin/env python3
"""Export Copilot CLI sessions from session-store.db to markdown files.

Reads ~/.copilot/session-store.db and writes one .md file per session
into i446-monorepo/ai-transcripts/copilot-cli/. Idempotent: skips
sessions that already have an exported file unless --force is passed.
"""

import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SESSION_STORE = Path.home() / ".copilot" / "session-store.db"
OUTPUT_DIR = Path.home() / "vault" / "i447" / "i446" / "ai-transcripts" / "copilot-cli"


def slugify(text: str, max_len: int = 60) -> str:
    if not text:
        return "untitled"
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:max_len].rstrip("-")


def format_date_prefix(iso_ts: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return "unknown-date"


def filename_for_session(session: dict) -> str:
    date = format_date_prefix(session["created_at"])
    slug = slugify(session["summary"])
    return f"{date}_{slug}.md"


def render_markdown(session: dict, turns: list, checkpoints: list) -> str:
    lines = []

    # YAML frontmatter
    lines.append("---")
    lines.append(f"session_id: {session['id']}")
    summary = session["summary"] or "Untitled"
    lines.append(f"summary: \"{summary}\"")
    lines.append(f"cwd: {session['cwd'] or 'unknown'}")
    if session.get("repository"):
        lines.append(f"repository: {session['repository']}")
    if session.get("branch"):
        lines.append(f"branch: {session['branch']}")
    lines.append(f"created: {session['created_at']}")
    lines.append(f"updated: {session['updated_at']}")
    lines.append(f"turns: {len(turns)}")
    lines.append("---")
    lines.append("")

    # Title
    lines.append(f"# {summary}")
    lines.append("")

    if not turns:
        lines.append("*No conversation turns recorded.*")
        lines.append("")
        return "\n".join(lines)

    # Conversation turns
    for turn in turns:
        ts = turn["timestamp"] or ""
        lines.append(f"## Turn {turn['turn_index']}")
        if ts:
            lines.append(f"*{ts}*")
        lines.append("")

        if turn["user_message"]:
            lines.append("**User:**")
            lines.append("")
            lines.append(turn["user_message"].strip())
            lines.append("")

        if turn["assistant_response"]:
            lines.append("**Assistant:**")
            lines.append("")
            lines.append(turn["assistant_response"].strip())
            lines.append("")

        lines.append("---")
        lines.append("")

    # Checkpoints (if any)
    if checkpoints:
        lines.append("## Checkpoints")
        lines.append("")
        for cp in checkpoints:
            lines.append(f"### Checkpoint {cp['checkpoint_number']}: {cp['title'] or 'Untitled'}")
            lines.append("")
            if cp.get("overview"):
                lines.append(cp["overview"].strip())
                lines.append("")
            if cp.get("work_done"):
                lines.append("**Work done:**")
                lines.append(cp["work_done"].strip())
                lines.append("")

    return "\n".join(lines)


def export_sessions(force: bool = False, verbose: bool = False):
    if not SESSION_STORE.exists():
        print(f"Error: session-store.db not found at {SESSION_STORE}", file=sys.stderr)
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{SESSION_STORE}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    sessions = conn.execute(
        "SELECT * FROM sessions ORDER BY created_at"
    ).fetchall()

    exported = 0
    skipped = 0

    for session in sessions:
        s = dict(session)
        fname = filename_for_session(s)
        outpath = OUTPUT_DIR / fname

        # Skip sessions with no turns (unless forced)
        turn_count = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = ?", (s["id"],)
        ).fetchone()[0]

        if turn_count == 0 and not force:
            if verbose:
                print(f"  skip (no turns): {fname}")
            skipped += 1
            continue

        if outpath.exists() and not force:
            if verbose:
                print(f"  skip (exists):   {fname}")
            skipped += 1
            continue

        turns = [dict(r) for r in conn.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_index",
            (s["id"],)
        ).fetchall()]

        checkpoints = [dict(r) for r in conn.execute(
            "SELECT * FROM checkpoints WHERE session_id = ? ORDER BY checkpoint_number",
            (s["id"],)
        ).fetchall()]

        md = render_markdown(s, turns, checkpoints)
        outpath.write_text(md, encoding="utf-8")
        exported += 1
        if verbose:
            print(f"  exported:        {fname}")

    conn.close()
    print(f"Exported {exported} session(s), skipped {skipped}.")


def main():
    parser = argparse.ArgumentParser(description="Export Copilot CLI transcripts to markdown.")
    parser.add_argument("--force", action="store_true", help="Re-export even if file exists")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show per-session details")
    args = parser.parse_args()
    export_sessions(force=args.force, verbose=args.verbose)


if __name__ == "__main__":
    main()
