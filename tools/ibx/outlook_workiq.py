#!/usr/bin/env python3
"""
outlook_workiq — Outlook emails via workiq natural language interface.
Read-only fetch via workiq. Write actions (archive/reply) via Gmail-to-Outlook
bridge: sends coded emails that Power Automate picks up and executes.
"""

import base64
import json
import os
import re
import subprocess
import webbrowser
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

from rich.console import Console

console = Console()

# Track which workiq items have been "processed" (archived/deleted in ibx)
PROCESSED_FILE = Path.home() / ".config" / "outlook" / "processed.json"

# Target Outlook address for the bridge emails
OUTLOOK_TARGET = os.environ.get("OUTLOOK_TARGET", "jomckay@microsoft.com")
# Gmail sender for bridge emails
BRIDGE_FROM = "mckay@m5x2.com"
# Subject prefix for bridge emails — PA filters on this
BRIDGE_PREFIX = "[IBX]"


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


WORKIQ_BIN = os.environ.get(
    "WORKIQ_BIN",
    str(Path.home() / ".agency/nodejs/node-v22.21.0-darwin-arm64/bin/workiq"),
)


def _run_workiq(question):
    """Run workiq ask and return the response text."""
    try:
        result = subprocess.run(
            [WORKIQ_BIN, "ask", "-q", question],
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
        "List every unread email in my inbox, including both Focused and Other tabs. "
        "For each one give me:\n"
        "FROM: full sender name and email address\n"
        "SUBJECT: full subject line\n"
        "BODY: the first 3 sentences of the email body, verbatim\n"
        "Separate each email with ---. Do not summarize, group, or omit any."
    )

    if not response or re.search(r'(?i)^NONE$|no unread|inbox is empty|no emails', response):
        console.print("  [dim]no unread emails[/dim]")
        return items

    # Detect workiq refusal / prompt-echo — these are not real emails
    refusal_signals = [
        r"(?i)why I'm stopping",
        r"(?i)I won't do that",
        r"(?i)tell me .* how you want",
        r"(?i)continuing without narrowing",
        r"(?i)omit nothing within the selected scope",
        r"(?i)explicitly rejected",
    ]
    if any(re.search(sig, response) for sig in refusal_signals):
        console.print("  [dim]workiq returned refusal — treating as empty[/dim]")
        return items

    processed = load_processed()

    # Extract any links from the full response for fallback
    all_links = _extract_outlook_links(response)
    link_idx = 0

    # Split into blocks by --- separators or by FROM: headers (with optional markdown bold)
    blocks = re.split(r'\n-{3,}\n', response)
    # If no --- separators, try splitting on FROM: lines
    if len(blocks) <= 1:
        blocks = re.split(r'\n(?=\*?\*?FROM:)', response)

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

        # Extract markdown footnote links [N](url) from this block
        block_links = _extract_outlook_links(block)

        for line in block.splitlines():
            line_clean = line.strip()
            # Strip markdown bold (**) wrapping field names
            bare = re.sub(r'\*\*', '', line_clean)
            if re.match(r'^FROM:', bare, re.IGNORECASE):
                from_str = re.sub(r'^FROM:\s*', '', bare, flags=re.IGNORECASE).strip()
            elif re.match(r'^TO:', bare, re.IGNORECASE):
                to_str = re.sub(r'^TO:\s*', '', bare, flags=re.IGNORECASE).strip()
            elif re.match(r'^SUBJECT:', bare, re.IGNORECASE):
                subject = re.sub(r'^SUBJECT:\s*', '', bare, flags=re.IGNORECASE).strip()
            elif re.match(r'^DATE:', bare, re.IGNORECASE):
                date_str = re.sub(r'^DATE:\s*', '', bare, flags=re.IGNORECASE).strip()
            elif re.match(r'^BODY:', bare, re.IGNORECASE):
                body = re.sub(r'^BODY:\s*', '', bare, flags=re.IGNORECASE).strip().strip('"')
            elif re.match(r'^LINK:', bare, re.IGNORECASE):
                link = re.sub(r'^LINK:\s*', '', bare, flags=re.IGNORECASE).strip()
            elif body and not re.match(r'^(?:FROM|TO|SUBJECT|DATE|LINK):', bare, re.IGNORECASE):
                body += " " + line_clean.strip('"')

        # Use block's footnote links if no explicit LINK: field
        if not link and block_links:
            link = block_links[0]
        # Last resort: grab from full response
        if not link and link_idx < len(all_links):
            link = all_links[link_idx]
            link_idx += 1

        if not from_str and not subject:
            continue

        # Skip placeholder/template entries (workiq echoing the prompt format)
        placeholder = re.compile(r'^[\s.…*_\-<>\[\]()]+$|^full\s|^first\s\d+\s')
        if all(placeholder.match(v) or not v for v in [from_str, subject, body]):
            continue

        item_id = _make_item_id(from_str, subject)

        # Skip already-processed items
        if item_id in processed:
            continue

        # Strip markdown link footnote refs from display fields
        from_str = re.sub(r'\s*\[\d+\]\([^)]+\)', '', from_str).strip()
        subject = re.sub(r'\s*\[\d+\]\([^)]+\)', '', subject).strip()
        body = re.sub(r'\s*\[\d+\]\([^)]+\)', '', body).strip()

        items.append({
            "type": "outlook",
            "source": "outlook",
            "from": from_str or "(unknown sender)",
            "to": to_str,
            "cc": "",
            "preview": subject or "(no subject)",
            "body": body or "(no body preview available via workiq)",
            "ts": 0.0,
            "_data": {
                "item_id": item_id,
                "link": link,
                "date": date_str,
                "email": {
                    "id": item_id,
                    "subject": subject or "(no subject)",
                    "from": from_str or "(unknown sender)",
                    "to": to_str,
                },
            },
        })

    console.print(f"  [dim]outlook: {len(items)} to review[/dim]")
    return items


def _get_gmail_service():
    """Get the Gmail service from ibx module (lazy import to avoid circular deps)."""
    import ibx as _ibx
    acct = _ibx.ACCOUNTS[0]  # m5c7 account
    return _ibx.get_gmail_service(acct["tokens"], acct["creds"])


def _send_bridge_email(action, subject, sender, reply_text=""):
    """Send a coded email from Gmail to Outlook that Power Automate will process.

    Subject format: [IBX] action | original_subject
    Body: JSON with sender + reply_text for PA to parse.
    """
    try:
        svc = _get_gmail_service()
    except Exception as e:
        console.print(f"[red]Gmail bridge auth failed: {e}[/red]")
        return False

    body_json = json.dumps({
        "sender": sender,
        "original_subject": subject,
        "action": action,
        "reply_text": reply_text,
    })

    msg = MIMEText(body_json)
    msg["To"] = OUTLOOK_TARGET
    msg["From"] = BRIDGE_FROM
    msg["Subject"] = f"{BRIDGE_PREFIX} {action} | {subject}"

    try:
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        svc.users().messages().send(userId="me", body={"raw": raw}).execute()
        return True
    except Exception as e:
        console.print(f"[red]Bridge send failed: {e}[/red]")
        return False


def _mark_processed(item_id):
    proc = load_processed()
    proc[item_id] = datetime.now().isoformat()
    save_processed(proc)


def archive(item_id, subject="", sender=""):
    """Archive via Gmail→PA bridge, then mark locally processed."""
    if subject and sender:
        _send_bridge_email("archive", subject, sender)
    _mark_processed(item_id)


def delete(item_id, subject="", sender=""):
    """Delete = archive for Outlook."""
    archive(item_id, subject, sender)


def reply(item_id, subject, sender, reply_text):
    """Reply via Gmail→PA bridge, then mark processed."""
    _send_bridge_email("reply", subject, sender, reply_text)
    _mark_processed(item_id)


def reply_all(item_id, subject, sender, reply_text):
    """Reply-all via Gmail→PA bridge, then mark processed."""
    _send_bridge_email("reply_all", subject, sender, reply_text)
    _mark_processed(item_id)


def pipe_through(item_id, subject, sender, body, instruction, reply_all_flag=False):
    """Use workiq to draft a reply from a natural language instruction, then send."""
    prompt = (
        f"Draft a short, professional email reply.\n"
        f"Original email from {sender}, subject: {subject}\n"
        f"Original body: {body[:1000]}\n"
        f"Instruction: {instruction}\n"
        f"Output ONLY the reply body text, no greeting preamble, no signature."
    )
    draft = _run_workiq(prompt)
    if not draft:
        console.print("[red]Failed to generate reply via workiq[/red]")
        return None
    draft = re.sub(r'\[\d+\]\([^)]+\)', '', draft).strip()
    action = "reply_all" if reply_all_flag else "reply"
    _send_bridge_email(action, subject, sender, draft)
    _mark_processed(item_id)
    return draft


def open_in_outlook(link):
    """Open the Outlook web link in the default browser."""
    if link:
        webbrowser.open(link)
    else:
        webbrowser.open("https://outlook.office365.com/mail/")


def reply_via_outlook(link):
    """Open in Outlook for manual reply (fallback)."""
    console.print("[dim](Opening in Outlook for reply...)[/dim]")
    open_in_outlook(link)
