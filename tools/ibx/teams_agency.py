#!/usr/bin/env python3
"""
teams_agency — Teams DMs via Agency MCP Teams server (Graph API).
Structured search for recent messages, reply via PostMessage, stable IDs.
"""

import json
import os
import re
import sys
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import subprocess

from rich.console import Console

console = Console()

PROCESSED_FILE = Path.home() / ".config" / "teams" / "processed.json"
MARKED_READ_FILE = Path.home() / ".config" / "teams" / "marked_read.json"
RESPONSE_DB = Path.home() / ".config" / "teams" / "response_times.db"
MY_ADDRESSES = {"jomckay@microsoft.com", "jonathan.mckay@microsoft.com"}

# Chat IDs already retried for mark-as-read this fetch cycle
_retry_read_chats = set()


def _load_marked_read():
    """Load set of chat_ids that have already been marked read via Chrome."""
    if MARKED_READ_FILE.exists():
        try:
            return set(json.load(open(MARKED_READ_FILE)))
        except (json.JSONDecodeError, TypeError):
            pass
    return set()


def _save_marked_read(chat_ids):
    """Persist chat_ids that have been marked read via Chrome."""
    MARKED_READ_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MARKED_READ_FILE, "w") as f:
        json.dump(list(chat_ids), f)


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
    conn = _init_response_db()
    now = datetime.now(timezone.utc)
    try:
        row = conn.execute("SELECT fetched_at FROM teams_responses WHERE item_id = ?", (item_id,)).fetchone()
        hours = None
        if row and row[0]:
            try:
                fetched = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
                if fetched.tzinfo is None:
                    fetched = fetched.replace(tzinfo=timezone.utc)
                raw = (now - fetched).total_seconds() / 3600
                # Drop responses outside the 72h matching window — those are
                # new threads, not replies. Within the window, clamp via the
                # daily PST midnight reset (max 24h).
                if 0 <= raw <= 72:
                    sys.path.insert(0, str(Path(__file__).parent.parent / "personal-dashboard"))
                    from comms_response_clamp import clamp_response_hours_dt
                    hours = round(clamp_response_hours_dt(now, fetched), 2)
            except Exception:
                pass
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


# ── Mark chat as read via Teams web in Chrome ─────────────────────────────────

# Chrome profile with Microsoft/Teams cookies
_CHROME_MSFT_PROFILE = "Profile 1"

# Fingerprint appended to URLs we open so _close_teams_tabs only kills ours
_IBX_TAB_MARKER = "ibx0mark=1"


def _mark_chat_read(chat_id):
    """Mark a Teams chat as read by opening it in Teams web (Chrome MSFT profile).

    The Graph API markChatReadForUser requires Chat.ReadWrite scope which
    isn't available via az CLI. The Teams MCP server has the scope but doesn't
    expose the tool. Opening the chat in Teams web (which has auth cookies)
    marks it as read and syncs to desktop. The tab is closed after loading.
    """
    if not chat_id:
        return
    # Verify user is in the chat before opening a tab
    check = _teams_call("GetChat", {"chatId": chat_id}, timeout=10)
    if check is None:
        return  # user not in chat or chat doesn't exist — skip silently
    import urllib.parse
    encoded = urllib.parse.quote(chat_id, safe='')
    url = f"https://teams.microsoft.com/l/chat/{encoded}?{_IBX_TAB_MARKER}"
    try:
        subprocess.run(
            ["open", "-na", "Google Chrome", "--args",
             f"--profile-directory={_CHROME_MSFT_PROFILE}", url],
            capture_output=True, timeout=5,
        )
    except Exception:
        console.print("[yellow]⚠ Could not open chat in Teams web — may still appear unread[/yellow]")


def close_ibx_teams_tabs():
    """Close only Chrome tabs opened by ibx0 (identified by ibx0mark param).
    Called once after all mark-as-read opens are done, not per-tab."""
    try:
        subprocess.run(
            ["osascript", "-e", f"""
tell application "Google Chrome"
    set winCount to count of windows
    repeat with w from winCount to 1 by -1
        try
            set tabCount to count of tabs of window w
            repeat with i from tabCount to 1 by -1
                try
                    if URL of tab i of window w contains "{_IBX_TAB_MARKER}" then
                        close tab i of window w
                    end if
                end try
            end repeat
        end try
    end repeat
end tell
"""],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


# ── Agency MCP Teams calls ────────────────────────────────────────────────────

def _teams_call(tool, args, timeout=30):
    """Call Agency Teams MCP tool. Returns content text or None."""
    import agency_mcp
    try:
        result = agency_mcp.call_tool("teams", tool, args, timeout=timeout)
        content = result.get("content", [])
        for c in content:
            if c.get("type") == "text":
                return c["text"]
        return ""
    except Exception as e:
        console.print(f"  [dim]teams MCP error: {e}[/dim]")
        return None


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_teams_items():
    """Fetch recent Teams DMs via Agency Teams MCP (Graph API search)."""
    global _retry_read_chats
    _retry_read_chats = set()  # reset per fetch cycle
    items = []
    console.print("\n[bold]Teams[/bold] — querying teams API...", style="dim")

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d")
    raw = _teams_call("SearchTeamMessagesQueryParameters", {
        "queryString": f"sent>={cutoff}",
        "size": 25,
    }, timeout=60)

    if raw is None:
        console.print("  [dim]teams API unavailable[/dim]")
        return items

    try:
        outer = json.loads(raw)
        inner = json.loads(outer.get("rawResponse", "{}"))
        hits_containers = inner.get("value", [{}])[0].get("hitsContainers", [{}])
        hits = hits_containers[0].get("hits", []) if hits_containers else []
    except (json.JSONDecodeError, IndexError, TypeError):
        console.print("  [dim]no recent Teams messages[/dim]")
        return items

    if not hits:
        console.print("  [dim]no recent Teams messages[/dim]")
        return items

    processed = load_processed()
    already_marked = _load_marked_read()

    # Collect per-chat unprocessed messages for grouping
    # chat_id -> list of (created, sender_name, summary, item_id, web_link, msg_id)
    chat_msgs = {}

    for hit in hits:
        resource = hit.get("resource", {})
        sender_data = resource.get("from", {}).get("emailAddress", {})
        sender_name = sender_data.get("name", "")
        sender_email = (sender_data.get("address") or "").lower()
        msg_id = resource.get("id", "")
        chat_id = resource.get("chatId", "")
        created = resource.get("createdDateTime", "")
        summary = hit.get("summary", "")
        web_link = resource.get("webLink", "")

        # Skip own messages
        if sender_email in MY_ADDRESSES:
            continue

        # Skip channel messages (only want 1:1 and group chats)
        channel_id = (resource.get("channelIdentity") or {}).get("channelId", "")
        if channel_id and "@thread.tacv2" in channel_id:
            continue

        # Clean summary (strip HTML tags)
        summary = re.sub(r'<[^>]+>', '', summary).strip()
        if not summary:
            continue

        item_id = f"teams:{chat_id}:{msg_id}"

        if item_id in processed:
            # Only mark-as-read for chats we haven't already marked
            if chat_id and chat_id not in _retry_read_chats and chat_id not in already_marked:
                _retry_read_chats.add(chat_id)
                _mark_chat_read(chat_id)
                already_marked.add(chat_id)
                _save_marked_read(already_marked)
            continue

        # Check legacy workiq-style IDs
        legacy_prefix = f"teams:{sender_name}:"
        summary_lower = summary[:30].lower()
        if any(k.startswith(legacy_prefix) and summary_lower in k.lower() for k in processed):
            processed[item_id] = datetime.now().isoformat()
            save_processed(processed)
            continue

        if chat_id not in chat_msgs:
            chat_msgs[chat_id] = []
        chat_msgs[chat_id].append((created, sender_name, summary, item_id, web_link, msg_id))

    # Group by chat_id: sort messages chronologically within each thread
    for chat_id, msgs in chat_msgs.items():
        msgs.sort(key=lambda m: m[0])  # sort by createdDateTime ascending
        latest = msgs[-1]
        latest_created, latest_sender, latest_summary, latest_item_id, latest_link, latest_msg_id = latest

        all_item_ids = [m[3] for m in msgs]

        # Record fetch only for the representative (latest) message
        record_fetch(latest_item_id, latest_sender, latest_summary[:40], latest_created)

        if len(msgs) == 1:
            body = latest_summary or "(no message text)"
        else:
            # Concatenate all messages chronologically
            lines = []
            for created, sender, summary, _iid, _link, _mid in msgs:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    time_str = dt.strftime("%I:%M %p")
                except Exception:
                    time_str = ""
                lines.append(f"{sender} ({time_str}): {summary}")
            body = "\n".join(lines)

        items.append({
            "type": "teams",
            "source": "teams",
            "from": latest_sender or "(unknown sender)",
            "to": "",
            "cc": "",
            "preview": f"Teams DM from {latest_sender}" if latest_sender else "(Teams DM)",
            "body": body,
            "ts": 0.0,
            "_data": {
                "item_id": latest_item_id,
                "all_item_ids": all_item_ids,
                "chat_id": chat_id,
                "msg_id": latest_msg_id,
                "link": latest_link,
                "date": latest_created,
                "sender": latest_sender,
                "message": latest_summary,
                "msg_count": len(msgs),
            },
        })

    # Sort by latest message date descending
    items.sort(key=lambda x: x["_data"].get("date", ""), reverse=True)

    console.print(f"  [dim]teams: {len(items)} to review[/dim]")

    # Batch cleanup: close all ibx0-opened tabs after they've had time to load
    if _retry_read_chats:
        import time
        def _deferred_close():
            time.sleep(5)
            close_ibx_teams_tabs()
        threading.Thread(target=_deferred_close, daemon=True).start()

    return items


# ── Actions ───────────────────────────────────────────────────────────────────

def archive(item_id, chat_id=""):
    """Mark Teams message as processed locally. Mark-as-read in Teams is
    handled lazily by fetch_teams_items when it detects stubborn unreads."""
    record_action(item_id, "archive")
    _mark_processed(item_id)


def archive_all(item_ids, chat_id=""):
    """Mark multiple Teams messages as processed (for grouped thread cards).
    Only records response-time action for the first (representative) ID."""
    if not item_ids:
        return
    # Record action only for the representative (latest) message
    record_action(item_ids[0], "archive")
    # Mark all individual messages as processed
    for iid in item_ids:
        _mark_processed(iid)


def delete(item_id, chat_id=""):
    archive(item_id, chat_id=chat_id)


def delete_all(item_ids, chat_id=""):
    """Delete (mark processed) multiple grouped messages."""
    archive_all(item_ids, chat_id=chat_id)


def reply(item_id, chat_id, reply_text):
    """Reply to Teams chat via Graph API."""
    if chat_id:
        result = _teams_call("PostMessage", {
            "chatId": chat_id,
            "content": reply_text,
            "contentType": "text",
        }, timeout=30)
        if result is None:
            console.print("[red]Teams reply failed[/red]")
            return False
    record_action(item_id, "reply")
    _mark_processed(item_id)
    return True


def reply_via_teams(link):
    if link:
        webbrowser.open(link)
    else:
        webbrowser.open("https://teams.microsoft.com/")


def open_in_teams(link):
    if link:
        webbrowser.open(link)
    else:
        webbrowser.open("https://teams.microsoft.com/")
