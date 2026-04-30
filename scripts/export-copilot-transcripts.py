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
SESSION_STORE_IX = Path.home() / ".copilot" / "session-store-ix.db"
OUTPUT_DIR = Path.home() / "vault" / "i447" / "i446" / "ai-transcripts" / "copilot-cli"
OUTPUT_DIR_IX = Path.home() / "vault" / "i447" / "i446" / "ai-transcripts" / "ix" / "copilot-cli"

# Host-aware sources. We can't rely on hostname (ix is "Jonathans-Mac-mini").
# The straylight box is the only one with a mirrored ix DB at SESSION_STORE_IX.
# Any other box exports its own DB into the ix/copilot-cli/ subdir.
if SESSION_STORE_IX.exists():
    SOURCES = [
        ("straylight", SESSION_STORE,    OUTPUT_DIR),
        ("ix",         SESSION_STORE_IX, OUTPUT_DIR_IX),
    ]
else:
    SOURCES = [
        ("ix", SESSION_STORE, OUTPUT_DIR_IX),
    ]


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


def _iso_to_epoch(iso_ts: str) -> float:
    try:
        return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError, TypeError):
        return 0.0


def export_sessions(force: bool = False, verbose: bool = False):
    available = [(host, db, out) for host, db, out in SOURCES if db.exists()]
    if not available:
        print(f"Error: no session-store DBs found "
              f"(checked {[str(s[1]) for s in SOURCES]})", file=sys.stderr)
        sys.exit(1)

    # Pass 1: discover canonical session per id (max turn count across DBs).
    chosen: dict[str, tuple[str, Path, dict, int]] = {}
    db_handles = {}
    for host, db_path, out_dir in available:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        db_handles[host] = (conn, out_dir)
        for row in conn.execute("SELECT * FROM sessions ORDER BY created_at"):
            s = dict(row)
            sid = s["id"]
            n = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE session_id = ?", (sid,)
            ).fetchone()[0]
            prior = chosen.get(sid)
            if prior is None or n > prior[3]:
                chosen[sid] = (host, db_path, s, n)

    exported = 0
    skipped = 0
    for sid, (host, db_path, s, turn_count) in chosen.items():
        conn, out_dir = db_handles[host]
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = filename_for_session(s)
        outpath = out_dir / fname

        if turn_count == 0 and not force:
            if verbose:
                print(f"  skip (no turns):   [{host}] {fname}")
            skipped += 1
            continue

        # Re-export when source updated_at is newer than file mtime.
        src_epoch = _iso_to_epoch(s.get("updated_at") or s.get("created_at") or "")
        if outpath.exists() and not force and src_epoch <= outpath.stat().st_mtime:
            if verbose:
                print(f"  skip (current):    [{host}] {fname}")
            skipped += 1
            continue

        turns = [dict(r) for r in conn.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_index",
            (sid,)
        ).fetchall()]

        checkpoints = [dict(r) for r in conn.execute(
            "SELECT * FROM checkpoints WHERE session_id = ? ORDER BY checkpoint_number",
            (sid,)
        ).fetchall()]

        md = render_markdown(s, turns, checkpoints)
        outpath.write_text(md, encoding="utf-8")
        exported += 1
        if verbose:
            print(f"  exported:          [{host}] {fname}")

    for conn, _ in db_handles.values():
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
