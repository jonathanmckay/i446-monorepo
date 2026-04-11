#!/usr/bin/env python3
"""
teams_workiq — Teams DMs via workiq natural language interface.
Read-only fetch via workiq. Write actions (reply) via Gmail-to-Outlook bridge
email, which Power Automate picks up and forwards to Teams.
"""

import base64
import json
import os
import re
import subprocess
import threading
import webbrowser
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

from rich.console import Console

console = Console()

PROCESSED_FILE = Path.home() / ".config" / "teams" / "processed.json"
RESPONSE_DB = Path.home() / ".config" / "teams" / "response_times.db"

WORKIQ_BIN = os.environ.get(
    "WORKIQ_BIN",
    str(Path.home() / ".agency/nodejs/node-v22.21.0-darwin-arm64/bin/workiq"),
)


def load_processed():
    PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    if PROCESSED_FILE.exists():
        with open(PROCESSED_FILE) as f:
            return json.load(f)
    return {}


def save_processed(proc):
    PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROCESSED_FILE, "w") as f:
        json.dump(proc, f)


def _run_workiq(question):
    """Run workiq ask and return the response text."""
    try:
        result = subprocess.run(
            [WORKIQ_BIN, "ask", "-q", question],
            capture_output=True, text=True, timeout=180,
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        console.print("  [dim]workiq timed out — skipping[/dim]")
        return ""
    except FileNotFoundError:
        console.print("  [dim]workiq not found — skipping[/dim]")
        return ""


def _extract_teams_links(text):
    """Extract Teams message URLs from workiq response."""
    md_links = re.findall(r'\[[^\]]*\]\((https://teams\.microsoft\.com/[^)]+)\)', text)
    bare_links = re.findall(r'(?<!\()(https://teams\.microsoft\.com/\S+)', text)
    seen = set()
    links = []
    for url in md_links + bare_links:
        url = url.rstrip(')')
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


def _make_item_id(from_str, message_preview):
    """Create a stable ID from sender + normalized message prefix for dedup."""
    import re as _re
    # Normalize: lowercase, collapse whitespace, strip punctuation variance
    normalized = _re.sub(r'\s+', ' ', message_preview.lower().strip())[:40]
    return f"teams:{from_str}:{normalized}"


def _init_response_db():
    """Initialize the response time tracking database."""
    import sqlite3
    RESPONSE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(RESPONSE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS teams_responses (
            item_id TEXT PRIMARY KEY,
            sender TEXT,
            preview TEXT,
            fetched_at TEXT,
            action TEXT,
            action_at TEXT,
            response_hours REAL
        )
    """)
    conn.commit()
    return conn


def record_fetch(item_id, sender, preview, received_at=None):
    """Record when a Teams message was first seen."""
    conn = _init_response_db()
    ts = received_at or datetime.now().isoformat()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO teams_responses (item_id, sender, preview, fetched_at) VALUES (?, ?, ?, ?)",
            (item_id, sender, preview, ts),
        )
        conn.commit()
    finally:
        conn.close()


def record_action(item_id, action):
    """Record when the user acted on a Teams message."""
    conn = _init_response_db()
    now = datetime.now()
    try:
        row = conn.execute("SELECT fetched_at FROM teams_responses WHERE item_id = ?", (item_id,)).fetchone()
        hours = None
        if row and row[0]:
            fetched = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            hours = round((now - fetched).total_seconds() / 3600, 2)
            if hours > 72:
                hours = None
        conn.execute(
            "UPDATE teams_responses SET action = ?, action_at = ?, response_hours = ? WHERE item_id = ?",
            (action, now.isoformat(), hours, item_id),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_processed(item_id):
    proc = load_processed()
    proc[item_id] = datetime.now().isoformat()
    save_processed(proc)


def fetch_teams_items():
    """Ask workiq for recent Teams DMs and parse into ibx-compatible items."""
    items = []
    console.print("\n[bold]Teams[/bold] — querying workiq...", style="dim")

    response = _run_workiq(
        "Show me my last 5 Teams 1:1 chat messages from other people (not from me). "
        "For each, write exactly:\n"
        "FROM: name\n"
        "SAYS: their message text\n"
        "Separate with ---"
    )

    if not response or re.search(r'(?i)^no (?:recent|unread|new)|no direct messages|no DMs|no 1:1 .* found', response):
        console.print("  [dim]no recent Teams DMs[/dim]")
        return items

    # Detect workiq refusal
    refusal_signals = [
        r"(?i)why I'm stopping",
        r"(?i)I won't do that",
        r"(?i)can't reliably complete",
    ]
    if any(re.search(sig, response) for sig in refusal_signals):
        # workiq hedges but still returns data — only bail if NO useful content follows
        if not re.search(r'(?i)\bFROM:', response):
            console.print("  [dim]workiq returned refusal — treating as empty[/dim]")
            return items

    processed = load_processed()
    all_links = _extract_teams_links(response)
    link_idx = 0

    blocks = re.split(r'\n-{3,}\n', response)
    if len(blocks) <= 1:
        blocks = re.split(r'\n(?=\*?\*?FROM:)', response)

    for block in blocks:
        block = block.strip()
        if not block or len(block) < 10:
            continue

        from_str = ""
        message = ""
        date_str = ""
        link = ""

        block_links = _extract_teams_links(block)

        for line in block.splitlines():
            line_clean = line.strip()
            bare = re.sub(r'\*\*', '', line_clean)
            if re.match(r'^FROM:', bare, re.IGNORECASE):
                from_str = re.sub(r'^FROM:\s*', '', bare, flags=re.IGNORECASE).strip()
            elif re.match(r'^DATE:', bare, re.IGNORECASE):
                date_str = re.sub(r'^DATE:\s*', '', bare, flags=re.IGNORECASE).strip()
            elif re.match(r'^(?:MESSAGE|SAYS):', bare, re.IGNORECASE):
                message = re.sub(r'^(?:MESSAGE|SAYS):\s*', '', bare, flags=re.IGNORECASE).strip().strip('"')
            elif message and not re.match(r'^(?:FROM|DATE|MESSAGE|SAYS|LINK):', bare, re.IGNORECASE):
                # Stop appending if we hit workiq meta-commentary
                if re.search(r'(?i)if you want|I can rerun|time window|specific people|truncated messages|I don.t |Also I ', bare):
                    break
                message += " " + line_clean.strip('"')

        if not link and block_links:
            link = block_links[0]
        if not link and link_idx < len(all_links):
            link = all_links[link_idx]
            link_idx += 1

        if not from_str and not message:
            continue

        # Skip own messages
        if re.search(r'(?i)jonathan\s+mckay|jomckay|mckay@', from_str):
            continue

        # Skip group chats — workiq sometimes includes them despite 1:1 prompt
        # Group chats have parenthetical names like "Name (Chat Title)" or "(group chat)"
        if re.search(r'\(.*(?:group|chat|workstream|weekly|standup|sync|1\|1|planning|pre-read)\)', from_str, re.IGNORECASE):
            continue

        # Clean markdown artifacts
        from_str = re.sub(r'\s*\[\d+\]\([^)]+\)', '', from_str).strip()
        message = re.sub(r'\s*\[\d+\]\([^)]+\)', '', message).strip()

        # Extract just the person name (strip parenthetical chat context)
        clean_name = re.sub(r'\s*\([^)]*\)\s*$', '', from_str).strip()
        if clean_name:
            from_str = clean_name

        # Skip items with no actual message text
        if not message:
            continue

        item_id = _make_item_id(from_str, message)

        if item_id in processed:
            continue

        # Also check legacy format (old 80-char keys)
        legacy_id = f"teams:{from_str}:{message[:80]}"
        if legacy_id in processed:
            processed[item_id] = datetime.now().isoformat()
            save_processed(processed)
            continue

        # Fuzzy check: if any processed key starts with same sender + first 30 chars
        msg_prefix = message[:30].lower().strip()
        if any(k.startswith(f"teams:{from_str}:") and msg_prefix in k.lower() for k in processed):
            processed[item_id] = datetime.now().isoformat()
            save_processed(processed)
            continue

        # Record fetch for response tracking
        record_fetch(item_id, from_str, message[:40])

        items.append({
            "type": "teams",
            "source": "teams",
            "from": from_str or "(unknown sender)",
            "to": "",
            "cc": "",
            "preview": f"Teams DM from {from_str}" if from_str else "(Teams DM)",
            "body": message or "(no message text available)",
            "ts": 0.0,
            "_data": {
                "item_id": item_id,
                "link": link,
                "date": date_str,
                "sender": from_str,
                "message": message,
            },
        })

    console.print(f"  [dim]teams: {len(items)} to review[/dim]")
    return items


def archive(item_id):
    """Mark Teams message as processed (local only — no server-side action)."""
    threading.Thread(
        target=lambda: (record_action(item_id, "archive"), _mark_processed(item_id)),
        daemon=True,
    ).start()


def delete(item_id):
    """Same as archive for Teams."""
    archive(item_id)


def reply_via_teams(link):
    """Open Teams in browser for manual reply."""
    if link:
        webbrowser.open(link)
    else:
        webbrowser.open("https://teams.microsoft.com/")


def open_in_teams(link):
    """Open the Teams message link."""
    if link:
        webbrowser.open(link)
    else:
        webbrowser.open("https://teams.microsoft.com/")
