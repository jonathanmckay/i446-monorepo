#!/usr/bin/env python3
"""Send the Dream morning brief via Gmail using the workspace MCP.

Usage: python3 dream-email-brief.py <run_dir>

Reads morning-brief.md from the run dir, converts to a clean email body,
and sends to JM's personal email via claude CLI (which has workspace MCP).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TO_EMAIL = "jonathan.b.mckay@gmail.com"
CLAUDE = "/opt/homebrew/bin/claude"


def main():
    if len(sys.argv) < 2:
        print("usage: dream-email-brief.py <run_dir>", file=sys.stderr)
        sys.exit(1)

    run_dir = Path(sys.argv[1])
    brief_path = run_dir / "morning-brief.md"

    if not brief_path.exists():
        print(f"No morning-brief.md in {run_dir}", file=sys.stderr)
        sys.exit(1)

    brief = brief_path.read_text()
    if not brief.strip():
        print("Morning brief is empty, skipping email", file=sys.stderr)
        sys.exit(0)

    # Extract version from run dir name (e.g., 2026.05.29-v20 → v20)
    version = run_dir.name.split("-")[-1] if "-" in run_dir.name else "dream"

    # Count cards
    card_count = brief.count("## Card") or brief.count("###")
    subject = f"Dream {version} morning brief ({card_count} cards)"

    # Truncate if too long for a prompt (keep first 8000 chars)
    body = brief[:8000]
    if len(brief) > 8000:
        body += "\n\n[truncated; full brief in vault]"

    prompt = (
        f'Send an email via the workspace MCP send_gmail_message tool. '
        f'To: {TO_EMAIL}. '
        f'Subject: {subject}. '
        f'Body (send as-is, do not rewrite):\n\n{body}'
    )

    try:
        result = subprocess.run(
            [CLAUDE, "-p", prompt, "--allowedTools",
             "mcp__workspace-mcp__send_gmail_message"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            print(f"Email sent: {subject}")
        else:
            print(f"Email send failed: {result.stderr[:200]}", file=sys.stderr)
            sys.exit(1)
    except subprocess.TimeoutExpired:
        print("Email send timed out", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
