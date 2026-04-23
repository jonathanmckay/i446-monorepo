#!/usr/bin/env python3
"""
sync_outlook_responses.py — Sync Outlook email response times via Microsoft Graph API.

Fetches sent items from the last N days, finds the original received message
in each conversation thread, computes response_hours, and upserts into
~/.config/outlook/response_times.db for the personal dashboard.

Uses the Agency MCP mail server (same auth as ibx/outlook_agency.py).

Usage:
    python3 sync_outlook_responses.py [--days 7]
"""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Import agency_mcp from ibx
sys.path.insert(0, str(Path(__file__).parent.parent / "ibx"))
import agency_mcp

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

RESPONSE_DB = Path.home() / ".config" / "outlook" / "response_times.db"
MY_EMAIL = "jomckay@microsoft.com"
MY_ALIASES = {"jomckay@microsoft.com", "jonathan.mckay@microsoft.com"}
# Original cap on the matching window; replies beyond this are considered
# new threads, not responses. Final response_hours is clamped daily via
# comms_response_clamp (max 24h, resets at PST midnight).
MATCH_WINDOW_HOURS = 72
# Assume Outlook "Sent:" timestamps in bodyPreview are Pacific time
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

# Shared midnight-PST clamp lives next to this script
sys.path.insert(0, str(Path(__file__).parent))
from comms_response_clamp import clamp_response_hours_dt


def init_db():
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


def mail_call(tool, args, timeout=30):
    """Call Agency mail MCP tool. Returns content text or None."""
    try:
        result = agency_mcp.call_tool("mail", tool, args, timeout=timeout)
        content = result.get("content", [])
        for c in content:
            if c.get("type") == "text":
                return c["text"]
        return ""
    except Exception as e:
        print(f"  WARN: mail MCP error: {e}")
        return None


def parse_graph_response(raw_text):
    """Parse the double-encoded Graph API response from SearchMessagesQueryParameters."""
    try:
        outer = json.loads(raw_text)
        raw_response = outer.get("rawResponse", "{}")
        inner = json.loads(raw_response) if isinstance(raw_response, str) else raw_response
        return inner.get("value", [])
    except (json.JSONDecodeError, TypeError):
        return []


def parse_iso_datetime(dt_str):
    """Parse an ISO datetime string from Graph API."""
    if not dt_str:
        return None
    dt_str = dt_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        return None


def parse_outlook_sent_date(sent_str):
    """Parse the 'Sent:' timestamp from Outlook's quoted reply header.

    Outlook formats: 'Friday, April 10, 2026 4:35 PM' or 'Thursday, April 10, 2026 10:32:15 PM'
    These are in the sender's local timezone. We assume Pacific for Microsoft internal.
    """
    if not sent_str:
        return None
    sent_str = sent_str.strip()
    # Try several common formats
    for fmt in [
        "%A, %B %d, %Y %I:%M %p",
        "%A, %B %d, %Y %I:%M:%S %p",
        "%B %d, %Y %I:%M %p",
        "%B %d, %Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %I:%M:%S %p",
        "%A, %B %d, %Y %H:%M",
        "%A, %B %d, %Y %H:%M:%S",
    ]:
        try:
            naive = datetime.strptime(sent_str, fmt)
            # Localize to Pacific and convert to UTC
            localized = naive.replace(tzinfo=LOCAL_TZ)
            return localized
        except ValueError:
            continue
    return None


def parse_quoted_headers(text):
    """Extract From and Sent from Outlook's quoted reply headers in body/bodyPreview.

    Returns (sender_email, sender_name, sent_datetime) or (None, None, None).
    """
    if not text:
        return None, None, None

    # Outlook puts a horizontal rule then From:/Sent:/To:/Subject: block
    # Match From: Name <email> or From: email
    from_match = re.search(
        r'From:\s*(.+?)(?:<([^>]+)>)?\s*[\r\n]',
        text,
    )
    sent_match = re.search(r'Sent:\s*(.+?)[\r\n]', text)

    if not from_match:
        return None, None, None

    sender_name = from_match.group(1).strip().rstrip("<").strip()
    sender_email = from_match.group(2)
    if not sender_email:
        # From line might just be an email
        if "@" in sender_name:
            sender_email = sender_name
            sender_name = ""

    sent_dt = None
    if sent_match:
        sent_dt = parse_outlook_sent_date(sent_match.group(1))

    return sender_email, sender_name, sent_dt


def fetch_sent_replies(days):
    """Fetch sent reply messages from the last N days. Returns list of message dicts."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_replies = []

    # Graph API doesn't support efficient from/emailAddress/address filter,
    # so we fetch all recent messages and filter client-side for our own replies.
    skip = 0
    page_size = 50
    max_pages = 10  # Safety limit: 500 messages max
    for _page in range(max_pages):
        query = (
            f"?$top={page_size}"
            f"&$skip={skip}"
            f"&$filter=receivedDateTime ge {cutoff}"
            f"&$select=id,subject,from,toRecipients,ccRecipients,sentDateTime,"
            f"receivedDateTime,conversationId,bodyPreview"
            f"&$orderby=receivedDateTime desc"
        )
        raw = mail_call("SearchMessagesQueryParameters", {
            "queryParameters": query,
        }, timeout=60)

        if raw is None:
            print("  ERROR: Could not fetch messages")
            break

        messages = parse_graph_response(raw)
        if not messages:
            break

        # Filter to replies sent by us
        for msg in messages:
            from_data = (msg.get("from") or {}).get("emailAddress", {})
            from_addr = (from_data.get("address") or "").lower()
            subject = msg.get("subject", "")

            if from_addr not in MY_ALIASES:
                continue
            if not subject.lower().startswith("re:"):
                continue
            # Skip calendar noise
            if any(kw in subject.lower() for kw in
                   ["accepted:", "declined:", "tentative:", "canceled:", "updated:"]):
                continue

            all_replies.append(msg)

        if len(messages) < page_size:
            break
        skip += page_size

    return all_replies


def get_message_details(msg_id):
    """Fetch full message details via GetMessage to get bodyPreview for header parsing."""
    raw = mail_call("GetMessage", {"id": msg_id}, timeout=30)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data.get("data", data)
    except (json.JSONDecodeError, TypeError):
        return None


def determine_action(msg):
    """Determine if a sent message is a reply or reply_all based on recipients."""
    to_recipients = msg.get("toRecipients", [])
    cc_recipients = msg.get("ccRecipients", [])

    # Handle both formats: list of dicts (Graph search) or list of strings (GetMessage)
    def count_non_self(recipients):
        count = 0
        for r in recipients:
            if isinstance(r, dict):
                addr = r.get("emailAddress", {}).get("address", "").lower()
            else:
                addr = str(r).lower()
            if addr not in MY_ALIASES:
                count += 1
        return count

    non_self_to = count_non_self(to_recipients)
    non_self_cc = count_non_self(cc_recipients)

    if non_self_to > 1 or non_self_cc > 0:
        return "reply_all"
    return "reply"


def sync_responses(days=7):
    """Main sync logic."""
    conn = init_db()

    # Get existing item_ids to skip
    existing = set()
    for row in conn.execute("SELECT item_id FROM outlook_responses WHERE action IS NOT NULL"):
        existing.add(row[0])

    print(f"Fetching sent replies from last {days} days...")
    replies = fetch_sent_replies(days)
    print(f"  Found {len(replies)} sent replies")

    # Filter out already processed
    new_replies = []
    for msg in replies:
        item_id = f"outlook-sent:{msg.get('id', '')}"
        if item_id not in existing:
            new_replies.append(msg)

    print(f"  {len(new_replies)} new replies to process")

    upserted = 0
    for msg in new_replies:
        msg_id = msg.get("id", "")
        item_id = f"outlook-sent:{msg_id}"
        subject = msg.get("subject", "")
        sent_dt_str = msg.get("sentDateTime") or msg.get("receivedDateTime", "")
        sent_dt = parse_iso_datetime(sent_dt_str)
        if not sent_dt:
            continue

        # Try to parse quoted headers from bodyPreview first
        body_preview = msg.get("bodyPreview", "")
        sender_email, sender_name, original_sent_dt = parse_quoted_headers(body_preview)

        # If bodyPreview was truncated and didn't have From:/Sent:, fetch full message
        if not sender_email:
            details = get_message_details(msg_id)
            if details:
                # Try bodyPreview from GetMessage (which may be fuller)
                bp = details.get("bodyPreview", "")
                sender_email, sender_name, original_sent_dt = parse_quoted_headers(bp)

                # Also try parsing from body HTML if bodyPreview fails
                if not sender_email:
                    body = details.get("body", "")
                    # Extract text between From: and Subject: in HTML
                    from_html = re.search(
                        r'<b>From:</b>\s*(.+?)(?:<br>|</)',
                        body,
                    )
                    sent_html = re.search(
                        r'<b>Sent:</b>\s*(.+?)(?:<br>|</)',
                        body,
                    )
                    if from_html:
                        # Strip HTML tags, then decode entities
                        from_text = re.sub(r'<[^>]+>', '', from_html.group(1)).strip()
                        from_text = from_text.replace("&lt;", "<").replace("&gt;", ">")
                        from_text = from_text.replace("&amp;", "&")
                        # Try to extract email from "Name <email>" pattern
                        em = re.search(r'<([^>]+@[^>]+)>', from_text)
                        if em:
                            sender_email = em.group(1)
                            sender_name = from_text.split("<")[0].strip()
                        elif "@" in from_text:
                            # Bare email address
                            email_match = re.search(r'[\w.+-]+@[\w.-]+', from_text)
                            if email_match:
                                sender_email = email_match.group(0)
                            else:
                                sender_email = from_text
                    if sent_html:
                        sent_text = re.sub(r'<[^>]+>', '', sent_html.group(1)).strip()
                        original_sent_dt = parse_outlook_sent_date(sent_text)

        if not sender_email:
            continue

        # Skip if the original sender is ourselves (e.g., forwarded chains)
        if sender_email.lower() in MY_ALIASES:
            continue

        if not original_sent_dt:
            continue

        # Compute response time
        # original_sent_dt is the time the original was sent (approx when we received it)
        delta_raw_hours = (sent_dt - original_sent_dt).total_seconds() / 3600
        # Skip negative or impossibly large (outside the matching window)
        if delta_raw_hours < 0:
            continue
        if delta_raw_hours > MATCH_WINDOW_HOURS:
            continue
        # Clamp via daily reset at PST midnight; final value <= 24h.
        response_hours = round(clamp_response_hours_dt(sent_dt, original_sent_dt), 2)

        # Format sender
        sender = f"{sender_name} <{sender_email}>" if sender_name else sender_email

        # Determine action type
        action = determine_action(msg)

        # fetched_at = original message sent time (best approx for when we received it)
        fetched_at = original_sent_dt.isoformat()

        # Upsert
        conn.execute("""
            INSERT INTO outlook_responses (item_id, sender, subject, fetched_at, action, action_at, response_hours)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                sender = excluded.sender,
                subject = excluded.subject,
                fetched_at = excluded.fetched_at,
                action = excluded.action,
                action_at = excluded.action_at,
                response_hours = excluded.response_hours
        """, (
            item_id,
            sender,
            subject,
            fetched_at,
            action,
            sent_dt_str,
            response_hours,
        ))
        upserted += 1

        print(f"  {action}: {subject[:60]}  ({response_hours:.1f}h) <- {sender_email}")

    conn.commit()
    conn.close()
    print(f"\nDone: {upserted} response(s) synced to {RESPONSE_DB}")


def main():
    parser = argparse.ArgumentParser(description="Sync Outlook email response times")
    parser.add_argument("--days", type=int, default=7, help="Days of sent items to scan (default: 7)")
    args = parser.parse_args()
    sync_responses(days=args.days)


if __name__ == "__main__":
    main()
