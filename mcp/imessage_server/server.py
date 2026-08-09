import datetime
import re
import sqlite3
import subprocess
import time
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


def _parse_typedstream_string(blob: bytes) -> str | None:
    """Precise parse of the NeXT "typedstream" length-prefixed string that
    follows the final NSString class marker in an attributedBody archive.

    Observed framing (from real chat.db samples): `NSString` (or the
    NSMutableString -> NSString chain) is followed by a short type-code run
    ending in `+`, then a length byte — either a raw byte (length < 128), or
    an 0x81 escape byte followed by the real length byte for 128-255 (with
    an extra NUL before the payload), or 0x82 + little-endian uint16 for
    longer strings — then exactly that many bytes of UTF-8 text.
    """
    idx = blob.rfind(b"NSString")
    if idx == -1:
        return None
    pos = idx + len(b"NSString")
    window = blob[pos:pos + 8]
    plus = window.find(b"+")
    if plus == -1:
        return None
    pos += plus + 1
    if pos >= len(blob):
        return None
    b0 = blob[pos]
    if b0 == 0x81:
        if pos + 1 >= len(blob):
            return None
        length = blob[pos + 1]
        pos += 2
        if pos < len(blob) and blob[pos] == 0x00:
            pos += 1
    elif b0 == 0x82:
        if pos + 2 >= len(blob):
            return None
        length = int.from_bytes(blob[pos + 1:pos + 3], "little")
        pos += 3
        if pos < len(blob) and blob[pos] == 0x00:
            pos += 1
    else:
        length = b0
        pos += 1
    if length <= 0 or pos + length > len(blob):
        return None
    try:
        text = blob[pos:pos + length].decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    return text or None


def _decode_attributed_body(blob: bytes) -> str | None:
    """Best-effort extraction of message text from the binary attributedBody
    column. Modern macOS stores the body as an NSAttributedString archive
    (typedstream/keyed-archiver hybrid) instead of populating the plain
    `text` column, so `text` is NULL for nearly every real iMessage/SMS.
    """
    if not blob:
        return None
    text = _parse_typedstream_string(blob)
    if text:
        return text
    # Fallback: longest printable run after the last class marker. Less
    # precise (can pick up a metadata key for very short real messages) but
    # better than nothing when the framing doesn't match the expected shape.
    idx = blob.rfind(b"NSString")
    if idx == -1:
        idx = blob.rfind(b"NSMutableString")
    if idx == -1:
        return None
    tail = blob[idx:].decode("utf-8", errors="ignore")
    runs = re.findall(r"[ -~]{1,}", tail)
    candidates = [r for r in runs if r not in ("NSString", "NSMutableString")]
    if not candidates:
        return None
    return max(candidates, key=len)


def _body_text(text: str | None, attributed_body: bytes | None) -> str | None:
    """Prefer the plain `text` column; fall back to attributedBody."""
    if text:
        return text
    return _decode_attributed_body(attributed_body)


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

    label = "Sent"
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
        label = "Sent (SMS)"

    # AppleScript returning success doesn't guarantee Messages actually
    # logged the send — confirm the row shows up in chat.db before
    # reporting success, so a real failure here is never silent.
    if _confirm_sent(recipient, message):
        return f"{label} to {recipient} (confirmed in chat.db)"
    return (
        f"{label} to {recipient}, but could NOT confirm it in chat.db — "
        f"AppleScript reported success but the message may not have gone through. "
        f"Do not resend automatically; check imessage_read first."
    )


def _confirm_sent(recipient: str, message: str, timeout: float = 5.0) -> bool:
    """Poll chat.db briefly for an outgoing message matching `message` to
    `recipient`, to turn a false "Sent" report into a real confirmation."""
    recipient = _normalize_recipient(recipient)
    deadline = time.time() + timeout
    needle = message.strip()[:40]
    while time.time() < deadline:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT ROWID FROM handle WHERE id = ? OR id LIKE ?",
                (recipient, f"%{recipient}%"),
            )
            handles = [row["ROWID"] for row in cur.fetchall()]
            if handles:
                placeholders = ",".join("?" * len(handles))
                cur.execute(
                    f"""
                    SELECT text, attributedBody FROM message
                    WHERE handle_id IN ({placeholders}) AND is_from_me = 1
                    ORDER BY date DESC LIMIT 5
                    """,
                    handles,
                )
                for row in cur.fetchall():
                    body = _body_text(row["text"], row["attributedBody"])
                    if body and body.strip()[:40] == needle:
                        conn.close()
                        return True
            conn.close()
        except Exception:
            pass
        time.sleep(0.5)
    return False


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
            SELECT m.text, m.attributedBody, m.date, m.is_from_me, h.id AS handle_id
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            WHERE m.handle_id IN ({placeholders})
            ORDER BY m.date DESC
            LIMIT ?
            """,
            (*handles, limit * 3),  # over-fetch since some rows will have no extractable body
        )
        rows = cur.fetchall()
        conn.close()

        parsed = []
        for row in rows:
            body = _body_text(row["text"], row["attributedBody"])
            if body:
                parsed.append((row, body))
            if len(parsed) >= limit:
                break

        if not parsed:
            return f"No messages found with {recipient}"

        lines = [f"# Conversation with {recipient} (last {len(parsed)} messages)\n"]
        for row, body in reversed(parsed):
            dt = _apple_ts_to_dt(row["date"])
            time_str = dt.strftime("%m/%d %H:%M")
            sender = "Me" if row["is_from_me"] else recipient
            lines.append(f"[{time_str}] {sender}: {body}")

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
                m.attributedBody AS last_attributed_body,
                m.date AS last_date,
                m.is_from_me
            FROM chat c
            JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
            JOIN message m ON cmj.message_id = m.ROWID
            WHERE m.date = (
                SELECT MAX(m2.date)
                FROM chat_message_join cmj2
                JOIN message m2 ON m2.ROWID = cmj2.message_id
                WHERE cmj2.chat_id = c.ROWID
            )
            GROUP BY c.ROWID
            ORDER BY m.date DESC
            LIMIT ?
            """,
            (limit * 3,),  # over-fetch since some rows will have no extractable body
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return "No conversations found."

        lines = ["# Recent Conversations\n"]
        count = 0
        for row in rows:
            body = _body_text(row["last_text"], row["last_attributed_body"])
            if not body:
                continue
            dt = _apple_ts_to_dt(row["last_date"])
            time_str = dt.strftime("%m/%d %H:%M")
            name = row["display_name"] or row["chat_identifier"]
            sender = "Me: " if row["is_from_me"] else ""
            preview = body[:60] + ("..." if len(body) > 60 else "")
            lines.append(f"[{time_str}] {name}  —  {sender}{preview}")
            count += 1
            if count >= limit:
                break

        return "\n".join(lines)

    except Exception as e:
        return f"Error reading conversations: {e}"
