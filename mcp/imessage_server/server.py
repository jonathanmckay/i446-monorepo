import datetime
import sqlite3
import subprocess
import os
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "iMessage",
    instructions="""Send and read iMessages/SMS on macOS.

- Use imessage_send to send a message to a phone number or email address.
- Use imessage_read to read recent messages from a conversation with a specific contact.
- Use imessage_conversations to list recent conversations.
- Recipients can be phone numbers (e.g. +14155551234) or email addresses.
""",
)

TZ = ZoneInfo("America/Los_Angeles")
DB_PATH = os.path.expanduser("~/Library/Messages/chat.db")

# Apple's epoch starts 2001-01-01; messages use nanoseconds on modern macOS
APPLE_EPOCH = datetime.datetime(2001, 1, 1, tzinfo=datetime.timezone.utc)


def _apple_ts_to_dt(ts: int) -> datetime.datetime:
    """Convert Apple timestamp (ns since 2001-01-01) to local datetime."""
    # macOS Catalina+ uses nanoseconds; older uses seconds
    if ts > 1e10:
        seconds = ts / 1e9
    else:
        seconds = float(ts)
    return (APPLE_EPOCH + datetime.timedelta(seconds=seconds)).astimezone(TZ)


def _normalize_recipient(recipient: str) -> str:
    """Strip spaces/dashes from phone numbers."""
    recipient = recipient.strip()
    if recipient.startswith("+") or "@" in recipient:
        return recipient
    digits = "".join(c for c in recipient if c.isdigit())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return recipient


@mcp.tool()
def imessage_send(recipient: str, message: str) -> str:
    """Send an iMessage or SMS to a phone number or email address.

    Args:
        recipient: Phone number (e.g. +14155551234 or 4155551234) or email address
        message: The message text to send
    """
    recipient = _normalize_recipient(recipient)

    script = f'''
tell application "Messages"
    set targetService to 1st service whose service type = iMessage
    set targetBuddy to buddy "{recipient}" of targetService
    send "{message}" to targetBuddy
end tell
'''
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        # Fall back to SMS
        script_sms = f'''
tell application "Messages"
    set targetService to 1st service whose service type = SMS
    set targetBuddy to buddy "{recipient}" of targetService
    send "{message}" to targetBuddy
end tell
'''
        result2 = subprocess.run(
            ["osascript", "-e", script_sms],
            capture_output=True,
            text=True,
        )
        if result2.returncode != 0:
            return f"Error sending message: {result.stderr.strip() or result2.stderr.strip()}"
        return f"Sent (SMS) to {recipient}"

    return f"Sent to {recipient}"


@mcp.tool()
def imessage_read(recipient: str, limit: int = 20) -> str:
    """Read recent messages in a conversation with a contact.

    Args:
        recipient: Phone number or email address of the contact
        limit: Number of recent messages to return (default 20)
    """
    recipient = _normalize_recipient(recipient)

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Find handle(s) matching this recipient
        cur.execute(
            "SELECT ROWID FROM handle WHERE id = ? OR id LIKE ?",
            (recipient, f"%{recipient}%"),
        )
        handles = [row["ROWID"] for row in cur.fetchall()]
        if not handles:
            conn.close()
            return f"No conversation found with {recipient}"

        placeholders = ",".join("?" * len(handles))
        cur.execute(
            f"""
            SELECT m.text, m.date, m.is_from_me, h.id AS handle_id
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            WHERE m.handle_id IN ({placeholders})
              AND m.text IS NOT NULL
              AND m.text != ''
            ORDER BY m.date DESC
            LIMIT ?
            """,
            (*handles, limit),
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return f"No messages found with {recipient}"

        lines = [f"# Conversation with {recipient} (last {min(limit, len(rows))} messages)\n"]
        for row in reversed(rows):
            dt = _apple_ts_to_dt(row["date"])
            time_str = dt.strftime("%m/%d %H:%M")
            sender = "Me" if row["is_from_me"] else recipient
            lines.append(f"[{time_str}] {sender}: {row['text']}")

        return "\n".join(lines)

    except Exception as e:
        return f"Error reading messages: {e}"


@mcp.tool()
def imessage_conversations(limit: int = 15) -> str:
    """List recent conversations with contact names/numbers and last message.

    Args:
        limit: Number of conversations to return (default 15)
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                c.chat_identifier,
                c.display_name,
                m.text AS last_text,
                m.date AS last_date,
                m.is_from_me
            FROM chat c
            JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
            JOIN message m ON cmj.message_id = m.ROWID
            WHERE m.text IS NOT NULL AND m.text != ''
            GROUP BY c.ROWID
            ORDER BY m.date DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return "No conversations found."

        lines = ["# Recent Conversations\n"]
        for row in rows:
            dt = _apple_ts_to_dt(row["last_date"])
            time_str = dt.strftime("%m/%d %H:%M")
            name = row["display_name"] or row["chat_identifier"]
            sender = "Me: " if row["is_from_me"] else ""
            preview = (row["last_text"] or "")[:60]
            if len(row["last_text"] or "") > 60:
                preview += "..."
            lines.append(f"[{time_str}] {name}  —  {sender}{preview}")

        return "\n".join(lines)

    except Exception as e:
        return f"Error reading conversations: {e}"
