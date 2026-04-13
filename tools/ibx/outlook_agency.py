#!/usr/bin/env python3
"""
outlook_agency — Outlook emails via Agency MCP mail server.
Full bidirectional: read inbox, reply, reply-all, archive, delete.
Falls back to outlook_workiq if Agency MCP is unavailable.
"""

import json
import os
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

console = Console()

PROCESSED_FILE = Path.home() / ".config" / "outlook" / "processed.json"
RESPONSE_DB = Path.home() / ".config" / "outlook" / "response_times.db"


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


# ── Response time tracking ────────────────────────────────────────────────────

def _init_response_db():
    import sqlite3
    RESPONSE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(RESPONSE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS outlook_responses (
            item_id TEXT PRIMARY KEY,
            sender TEXT,
            subject TEXT,
            fetched_at TEXT,
            action TEXT,
            action_at TEXT,
            response_hours REAL
        )
    """)
    conn.commit()
    return conn


def record_fetch(item_id, sender, subject, received_at=None):
    conn = _init_response_db()
    ts = received_at or datetime.now().isoformat()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO outlook_responses (item_id, sender, subject, fetched_at) VALUES (?, ?, ?, ?)",
            (item_id, sender, subject, ts),
        )
        conn.commit()
    finally:
        conn.close()


def record_action(item_id, action):
    conn = _init_response_db()
    now = datetime.now(timezone.utc)
    try:
        row = conn.execute("SELECT fetched_at FROM outlook_responses WHERE item_id = ?", (item_id,)).fetchone()
        hours = None
        if row and row[0]:
            try:
                fetched = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
                if fetched.tzinfo is None:
                    fetched = fetched.replace(tzinfo=timezone.utc)
                hours = round((now - fetched).total_seconds() / 3600, 2)
                if hours > 72:
                    hours = None
            except Exception:
                pass
        conn.execute(
            "UPDATE outlook_responses SET action = ?, action_at = ?, response_hours = ? WHERE item_id = ?",
            (action, now.isoformat(), hours, item_id),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_processed(item_id):
    proc = load_processed()
    proc[item_id] = datetime.now().isoformat()
    save_processed(proc)


# ── Agency MCP mail calls ─────────────────────────────────────────────────────

def _mail_call(tool, args, timeout=30):
    """Call Agency mail MCP tool. Returns content text or None."""
    import agency_mcp
    try:
        result = agency_mcp.call_tool("mail", tool, args, timeout=timeout)
        content = result.get("content", [])
        for c in content:
            if c.get("type") == "text":
                return c["text"]
        return ""
    except Exception as e:
        console.print(f"  [dim]mail MCP error: {e}[/dim]")
        return None


def _parse_graph_messages(raw_text):
    """Parse the double-encoded Graph API response from SearchMessagesQueryParameters."""
    try:
        outer = json.loads(raw_text)
        raw_response = outer.get("rawResponse", "{}")
        inner = json.loads(raw_response) if isinstance(raw_response, str) else raw_response
        return inner.get("value", [])
    except (json.JSONDecodeError, TypeError):
        return []


_inbox_folder_id_cache = None


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_outlook_items():
    """Fetch recent unread Outlook emails via Agency mail MCP."""
    items = []
    console.print("\n[bold]Outlook[/bold] — querying mail API...", style="dim")

    from datetime import timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = (
        f"?$top=30"
        f"&$filter=isRead eq false and receivedDateTime ge {cutoff}"
        f"&$select=id,subject,from,receivedDateTime,bodyPreview,parentFolderId"
        f"&$orderby=receivedDateTime desc"
    )
    raw = _mail_call("SearchMessagesQueryParameters", {
        "queryParameters": query
    }, timeout=30)

    if raw is None:
        console.print("  [dim]mail API unavailable[/dim]")
        return items

    messages = _parse_graph_messages(raw)
    if not messages:
        console.print("  [dim]no recent emails[/dim]")
        return items

    processed = load_processed()

    # Calendar/meeting response patterns to filter out
    import re
    CALENDAR_RE = re.compile(
        r'^(\[EXTERNAL\]\s*)?(Accepted|Declined|Tentative|Canceled|Updated|Forwarded):\s',
        re.IGNORECASE,
    )
    # Skip emails sent by the user (Sent Items folder)
    MY_ADDRESSES = {"jomckay@microsoft.com", "jonathan.mckay@microsoft.com"}
    # Automated/notification senders to skip (Outlook "Other" tab noise)
    NOISE_SENDERS = {
        "msaemail@microsoft.com",       # MSApprovals
        "msxemail@microsoft.com",       # MyExpense
        "noreply@microsoft.com",        # SharePoint, system notifications
        "noreply@email.teams.microsoft.com",
        "benefits@microsoft.com",
        "weeklyfeed@microsoft.com",
        "powerautomatenorepley@microsoft.com",
        "microsoftexchangeonline@microsoft.com",
    }
    # Subject patterns for automated notifications
    NOISE_SUBJECT_RE = re.compile(
        r'^(\[EXTERNAL\]\s*)?(Reminder:|Expense Report|Your sitter job|Supplement your|RE: ﻿Post)',
        re.IGNORECASE,
    )

    for msg in messages:
        msg_id = msg.get("id", "")
        subject = msg.get("subject") or "(no subject)"
        from_data = (msg.get("from") or {}).get("emailAddress") or {}
        sender_name = from_data.get("name") or ""
        sender_email = (from_data.get("address") or "").lower()
        from_str = f"{sender_name} <{sender_email}>" if sender_name else sender_email
        body_preview = msg.get("bodyPreview") or ""
        received = msg.get("receivedDateTime") or ""

        # Skip own sent emails
        if sender_email in MY_ADDRESSES:
            continue

        # Skip bridge emails — and auto-clean them
        if "[IBX]" in subject:
            threading.Thread(
                target=lambda mid=msg_id: _mail_call("DeleteMessage", {"id": mid}, timeout=15),
                daemon=True,
            ).start()
            continue

        # Skip calendar/meeting responses
        if CALENDAR_RE.match(subject):
            continue

        # Skip automated notification senders
        if sender_email in NOISE_SENDERS:
            continue

        # Skip automated notification subjects
        if NOISE_SUBJECT_RE.match(subject):
            continue

        item_id = f"outlook:{msg_id}"

        if item_id in processed:
            continue

        # Also check legacy workiq-style IDs for backwards compat
        legacy_id = f"workiq:{from_str}:{subject}"
        legacy_id2 = f"workiq:{sender_name}:{subject}"
        if legacy_id in processed or legacy_id2 in processed:
            # Migrate to new ID format
            processed[item_id] = datetime.now().isoformat()
            save_processed(processed)
            continue

        # Record with actual receive time from Graph API
        record_fetch(item_id, from_str, subject, received)

        items.append({
            "type": "outlook",
            "source": "outlook",
            "from": from_str or "(unknown sender)",
            "to": "",
            "cc": "",
            "preview": subject,
            "body": body_preview or "(no body preview)",
            "ts": 0.0,
            "_data": {
                "item_id": item_id,
                "graph_id": msg_id,
                "link": "",
                "date": received,
                "email": {
                    "id": item_id,
                    "subject": subject,
                    "from": from_str or "(unknown sender)",
                    "to": "",
                },
            },
        })

    console.print(f"  [dim]outlook: {len(items)} to review[/dim]")
    return items


# ── Actions ───────────────────────────────────────────────────────────────────

def archive(item_id, subject="", sender=""):
    """Archive email — delete from inbox via Graph API so it won't reappear."""
    graph_id = item_id.replace("outlook:", "", 1)
    if graph_id:
        def _do_archive():
            _mail_call("DeleteMessage", {"id": graph_id}, timeout=15)
        threading.Thread(target=_do_archive, daemon=True).start()
    record_action(item_id, "archive")
    _mark_processed(item_id)


def delete(item_id, subject="", sender=""):
    """Delete email via Graph API."""
    graph_id = item_id.replace("outlook:", "", 1)
    if graph_id:
        threading.Thread(
            target=lambda: _mail_call("DeleteMessage", {"id": graph_id}, timeout=15),
            daemon=True,
        ).start()
    record_action(item_id, "archive")
    _mark_processed(item_id)


def reply(item_id, subject, sender, reply_text):
    """Reply to email via Graph API, then archive it from the inbox."""
    graph_id = item_id.replace("outlook:", "", 1)
    if graph_id:
        result = _mail_call("ReplyToMessage", {
            "id": graph_id,
            "comment": reply_text,
            "sendImmediately": True,
        }, timeout=30)
        if result is None:
            console.print("[red]Reply failed[/red]")
            return
        # Archive the email so it leaves the inbox after replying
        threading.Thread(
            target=lambda: _mail_call("DeleteMessage", {"id": graph_id}, timeout=15),
            daemon=True,
        ).start()
    record_action(item_id, "reply")
    _mark_processed(item_id)


def reply_all(item_id, subject, sender, reply_text):
    """Reply-all to email via Graph API, then archive it from the inbox."""
    graph_id = item_id.replace("outlook:", "", 1)
    if graph_id:
        result = _mail_call("ReplyAllToMessage", {
            "id": graph_id,
            "comment": reply_text,
            "sendImmediately": True,
        }, timeout=30)
        if result is None:
            console.print("[red]Reply-all failed[/red]")
            return
        threading.Thread(
            target=lambda: _mail_call("DeleteMessage", {"id": graph_id}, timeout=15),
            daemon=True,
        ).start()
    record_action(item_id, "reply_all")
    _mark_processed(item_id)


def pipe_through(item_id, subject, sender, body, instruction, reply_all_flag=False):
    """Use Claude to draft a reply, then send via Graph API."""
    try:
        import anthropic
        ai = anthropic.Anthropic()
        prompt = (
            f"Draft a short, professional email reply.\n"
            f"Original email from {sender}, subject: {subject}\n"
            f"Original body: {body[:1000]}\n"
            f"Instruction: {instruction}\n"
            f"Output ONLY the reply body text, no greeting preamble, no signature."
        )
        msg = ai.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        draft = msg.content[0].text.strip()
    except Exception as e:
        console.print(f"[red]Draft generation failed: {e}[/red]")
        return None

    if reply_all_flag:
        reply_all(item_id, subject, sender, draft)
    else:
        reply(item_id, subject, sender, draft)
    return draft


def open_in_outlook(link):
    if link:
        webbrowser.open(link)
    else:
        webbrowser.open("https://outlook.office365.com/mail/")


def reply_via_outlook(link):
    console.print("[dim](Opening in Outlook for reply...)[/dim]")
    open_in_outlook(link)
