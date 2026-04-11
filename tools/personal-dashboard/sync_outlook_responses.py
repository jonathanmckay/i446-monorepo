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
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Import agency_mcp from ibx
sys.path.insert(0, str(Path(__file__).parent.parent / "ibx"))
import agency_mcp

RESPONSE_DB = Path.home() / ".config" / "outlook" / "response_times.db"
MY_EMAIL = "jomckay@microsoft.com"
MAX_RESPONSE_HOURS = 72


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


def fetch_sent_items(days):
    """Fetch sent items from the last N days. Returns list of message dicts."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_messages = []

    # Graph API doesn't support efficient from/emailAddress/address filter,
    # so we fetch all recent messages and filter client-side for our own.
    skip = 0
    page_size = 50
    while True:
        query = (
            f"?$top={page_size}"
            f"&$skip={skip}"
            f"&$filter=receivedDateTime ge {cutoff}"
            f"&$select=id,subject,from,toRecipients,ccRecipients,sentDateTime,"
            f"receivedDateTime,conversationId,internetMessageId"
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

        # Filter to messages sent by us
        for msg in messages:
            from_data = (msg.get("from") or {}).get("emailAddress", {})
            from_addr = (from_data.get("address") or "").lower()
            if from_addr == MY_EMAIL:
                all_messages.append(msg)

        if len(messages) < page_size:
            break
        skip += page_size

    return all_messages


def fetch_conversation_messages(conversation_id, sent_datetime):
    """Fetch messages in a conversation to find the original received message.

    Returns list of message dicts in the conversation, ordered by receivedDateTime asc.
    We look for messages received before the sent_datetime that are NOT from us.
    """
    # Query all messages with this conversationId, selecting key fields
    # Filter to messages received before our sent time
    query = (
        f"?$top=20"
        f"&$filter=conversationId eq '{conversation_id}'"
        f"&$select=id,subject,from,receivedDateTime,conversationId"
        f"&$orderby=receivedDateTime desc"
    )

    raw = mail_call("SearchMessagesQueryParameters", {
        "queryParameters": query,
    }, timeout=30)

    if raw is None:
        return []

    return parse_graph_response(raw)


def determine_action(sent_msg):
    """Determine if a sent message is a reply or reply_all based on recipients."""
    to_recipients = sent_msg.get("toRecipients", [])
    cc_recipients = sent_msg.get("ccRecipients", [])

    # Count non-self recipients
    non_self_to = [
        r for r in to_recipients
        if (r.get("emailAddress", {}).get("address", "").lower() != MY_EMAIL)
    ]
    non_self_cc = [
        r for r in cc_recipients
        if (r.get("emailAddress", {}).get("address", "").lower() != MY_EMAIL)
    ]

    if len(non_self_to) > 1 or non_self_cc:
        return "reply_all"
    return "reply"


def parse_datetime(dt_str):
    """Parse an ISO datetime string from Graph API."""
    if not dt_str:
        return None
    # Graph API returns UTC times like "2026-04-10T15:37:35Z"
    dt_str = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(dt_str)


def sync_responses(days=7):
    """Main sync logic."""
    conn = init_db()

    # Get existing item_ids to skip
    existing = set()
    for row in conn.execute("SELECT item_id FROM outlook_responses WHERE action IS NOT NULL"):
        existing.add(row[0])

    print(f"Fetching sent items from last {days} days...")
    sent_items = fetch_sent_items(days)
    print(f"  Found {len(sent_items)} sent items")

    # Filter to replies (subject starts with "Re:" or "RE:")
    replies = []
    for msg in sent_items:
        subject = msg.get("subject", "")
        item_id = f"outlook-sent:{msg.get('id', '')}"

        # Skip already processed
        if item_id in existing:
            continue

        # Only process replies
        if not subject.lower().startswith("re:"):
            continue

        # Skip calendar/meeting responses
        if any(kw in subject.lower() for kw in ["accepted:", "declined:", "tentative:", "canceled:"]):
            continue

        replies.append(msg)

    print(f"  {len(replies)} new replies to process")

    upserted = 0
    for msg in replies:
        msg_id = msg.get("id", "")
        item_id = f"outlook-sent:{msg_id}"
        subject = msg.get("subject", "")
        conversation_id = msg.get("conversationId", "")
        sent_dt_str = msg.get("sentDateTime") or msg.get("receivedDateTime", "")

        sent_dt = parse_datetime(sent_dt_str)
        if not sent_dt:
            continue

        # Find the original message we replied to
        if not conversation_id:
            continue

        conv_messages = fetch_conversation_messages(conversation_id, sent_dt_str)
        if not conv_messages:
            continue

        # Find the most recent message from someone else, received before our reply
        original = None
        for conv_msg in conv_messages:
            from_data = (conv_msg.get("from") or {}).get("emailAddress", {})
            from_addr = (from_data.get("address") or "").lower()

            # Skip our own messages
            if from_addr == MY_EMAIL:
                continue

            recv_dt = parse_datetime(conv_msg.get("receivedDateTime", ""))
            if not recv_dt:
                continue

            # Must be before our sent time
            if recv_dt >= sent_dt:
                continue

            # Take the most recent one (list is desc by receivedDateTime)
            if original is None:
                original = {
                    "from": from_data,
                    "received_dt": recv_dt,
                    "recv_dt_str": conv_msg.get("receivedDateTime", ""),
                }
                break

        if original is None:
            continue

        # Compute response time
        delta = sent_dt - original["received_dt"]
        response_hours = round(delta.total_seconds() / 3600, 2)

        # Cap at MAX_RESPONSE_HOURS
        if response_hours > MAX_RESPONSE_HOURS:
            response_hours = MAX_RESPONSE_HOURS
        if response_hours < 0:
            continue

        # Determine sender
        from_data = original["from"]
        sender_name = from_data.get("name", "")
        sender_addr = from_data.get("address", "")
        sender = f"{sender_name} <{sender_addr}>" if sender_name else sender_addr

        # Determine action type
        action = determine_action(msg)

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
            original["recv_dt_str"],
            action,
            sent_dt_str,
            response_hours,
        ))
        upserted += 1

        print(f"  {action}: {subject[:60]}  ({response_hours:.1f}h) <- {sender_addr}")

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
