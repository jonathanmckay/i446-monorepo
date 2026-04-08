#!/usr/bin/env python3
"""
outlook_workiq — Outlook emails via workiq natural language interface.
Read-only: fetches email summaries, renders as ibx cards.
Actions open Outlook in browser since we lack Graph API write access.
"""

import json
import os
import re
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path

from rich.console import Console

console = Console()

# Track which workiq items have been "processed" (archived/deleted in ibx)
PROCESSED_FILE = Path.home() / ".config" / "outlook" / "processed.json"


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
            ["workiq", "ask", "-q", question],
            capture_output=True, text=True, timeout=120,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        console.print(f"  [yellow]workiq error: {e}[/yellow]")
        return ""


def _extract_outlook_links(text):
    """Extract Outlook web URLs from workiq response."""
    # Markdown links: [N](url)
    md_links = re.findall(r'\[[^\]]*\]\((https://outlook\.office365?\.com/[^)]+)\)', text)
    # Bare URLs
    bare_links = re.findall(r'(?<!\()(https://outlook\.office365?\.com/\S+)', text)
    # Deduplicate preserving order
    seen = set()
    links = []
    for url in md_links + bare_links:
        url = url.rstrip(')')
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


def _make_item_id(from_str, subject):
    """Create a stable-ish ID from sender + subject for dedup."""
    return f"workiq:{from_str}:{subject}"


def fetch_outlook_items():
    """Ask workiq for recent Outlook emails and parse into ibx-compatible items."""
    items = []
    console.print("\n[bold]Outlook[/bold] — querying workiq...", style="dim")

    response = _run_workiq(
        "List my 20 most recent unread emails from my Outlook inbox. "
        "For each email, output in this exact format with one blank line between each:\n\n"
        "FROM: sender name <sender@email.com>\n"
        "TO: recipient name <recipient@email.com>\n"
        "SUBJECT: the subject line\n"
        "DATE: the date/time received\n"
        "BODY: first 2-3 sentences of the email body\n"
        "LINK: direct Outlook link to open this email\n\n"
        "If there are no unread emails, output exactly: NONE"
    )

    if not response or re.search(r'(?i)^NONE$|no unread|inbox is empty|no emails', response):
        console.print("  [dim]no unread emails[/dim]")
        return items

    processed = load_processed()

    # Extract any links from the full response for fallback
    all_links = _extract_outlook_links(response)
    link_idx = 0

    # Split into blocks by double newline or by FROM: headers
    blocks = re.split(r'\n(?=FROM:)', response)

    for block in blocks:
        block = block.strip()
        if not block or len(block) < 10:
            continue

        from_str = ""
        to_str = ""
        subject = ""
        date_str = ""
        body = ""
        link = ""

        for line in block.splitlines():
            if line.startswith("FROM:"):
                from_str = line[5:].strip()
            elif line.startswith("TO:"):
                to_str = line[3:].strip()
            elif line.startswith("SUBJECT:"):
                subject = line[8:].strip()
            elif line.startswith("DATE:"):
                date_str = line[5:].strip()
            elif line.startswith("BODY:"):
                body = line[5:].strip()
            elif line.startswith("LINK:"):
                link = line[5:].strip()
            elif body and not line.startswith(("FROM:", "TO:", "SUBJECT:", "DATE:", "LINK:")):
                body += " " + line.strip()

        if not from_str and not subject:
            continue

        # Extract link from markdown if structured LINK: was empty
        if not link:
            block_links = _extract_outlook_links(block)
            if block_links:
                link = block_links[0]
            elif link_idx < len(all_links):
                link = all_links[link_idx]
                link_idx += 1

        item_id = _make_item_id(from_str, subject)

        # Skip already-processed items
        if item_id in processed:
            continue

        items.append({
            "type": "outlook",
            "source": "outlook",
            "from": from_str,
            "to": to_str,
            "cc": "",
            "preview": subject or "(no subject)",
            "body": body or "(no body preview available)",
            "ts": 0.0,
            "_data": {
                "item_id": item_id,
                "link": link,
                "date": date_str,
                "email": {
                    "id": item_id,
                    "subject": subject,
                    "from": from_str,
                    "to": to_str,
                },
            },
        })

    console.print(f"  [dim]outlook: {len(items)} to review[/dim]")
    return items


def archive(item_id):
    """Mark item as processed (can't actually archive via workiq)."""
    proc = load_processed()
    proc[item_id] = datetime.now().isoformat()
    save_processed(proc)


def delete(item_id):
    """Mark item as processed."""
    archive(item_id)


def open_in_outlook(link):
    """Open the Outlook web link in the default browser."""
    if link:
        webbrowser.open(link)
    else:
        webbrowser.open("https://outlook.office365.com/mail/")


def reply_via_outlook(link):
    """Open in Outlook for manual reply (can't send via workiq)."""
    console.print("[dim](Opening in Outlook for reply...)[/dim]")
    open_in_outlook(link)
