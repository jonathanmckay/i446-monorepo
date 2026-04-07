#!/usr/bin/env python3
"""
ibx_status — Fast inbox count check (no TUI, no interactive fetch).
Outputs JSON: {"email": N, "imsg": N, "slack": 0, "total": N}

Sources:
  Gmail   — labels().get(INBOX) for messagesUnread (one lightweight API call per account)
  iMessage — direct SQLite query on chat.db snapshot (local, ~50ms)
  Slack   — always 0 (no persistent Slack watcher configured)
"""

import json
import shutil
import sqlite3
import sys
import warnings
warnings.filterwarnings("ignore")
from pathlib import Path

# ── iMessage ──────────────────────────────────────────────────────────────────

CHAT_DB = Path.home() / "Library/Messages/chat.db"
DB_SNAPSHOT = Path("/tmp/ibx_status_imsg.db")
IMSG_PROCESSED = Path.home() / ".config/imsg/processed.json"


def count_imessage() -> int:
    if not CHAT_DB.exists():
        return 0
    try:
        shutil.copy2(CHAT_DB, DB_SNAPSHOT)
        conn = sqlite3.connect(str(DB_SNAPSHOT))
        rows = conn.execute("""
            SELECT c.chat_identifier, MAX(m.date) as latest_date
            FROM chat c
            JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
            JOIN message m ON cmj.message_id = m.ROWID
            WHERE m.is_read = 0 AND m.is_from_me = 0
            GROUP BY c.ROWID
        """).fetchall()
        conn.close()
        processed = {}
        if IMSG_PROCESSED.exists():
            try:
                data = json.loads(IMSG_PROCESSED.read_text())
                if isinstance(data, dict):
                    processed = data
                # list format: treat as empty — all unread threads count
            except Exception:
                pass
        return sum(
            1 for chat_id, latest_date in rows
            if latest_date > processed.get(chat_id, -1)
        )
    except Exception:
        return 0


# ── Gmail ─────────────────────────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".config" / "eml"
ACCOUNTS = [
    {"name": "m5c7",  "tokens": "tokens.json",       "creds": "gcp-oauth.keys.json"},
    {"name": "gmail", "tokens": "tokens-gmail.json",  "creds": "gcp-oauth-gmail.keys.json"},
]
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def get_service(tokens_file: str):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        tokens_path = CONFIG_DIR / tokens_file
        if not tokens_path.exists():
            return None
        data = json.loads(tokens_path.read_text())
        creds = Credentials(
            token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scopes=SCOPES,
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            tokens_path.write_text(json.dumps({
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
            }))
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception:
        return None


def count_gmail() -> int:
    total = 0
    for acct in ACCOUNTS:
        svc = get_service(acct["tokens"])
        if not svc:
            continue
        try:
            label = svc.users().labels().get(userId="me", id="INBOX").execute()
            total += label.get("messagesUnread", 0)
        except Exception:
            pass
    return total


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    email = count_gmail()
    imsg = count_imessage()
    slack = 0
    total = email + imsg + slack
    print(json.dumps({"email": email, "imsg": imsg, "slack": slack, "total": total}))


if __name__ == "__main__":
    main()
