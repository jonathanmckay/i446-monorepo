#!/usr/bin/env python3
"""sync_external_replies.py — Poll Slack, Gmail, Outlook, and Teams for sent
replies and insert them into archive_log.db (the unified response tracker).

Runs every 30 min via cron. Idempotent: uses INSERT OR IGNORE with unique
item_uid+action keys.

Usage:
    python3 sync_external_replies.py [--lookback-hours N]  (default: 24)
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Add project paths for imports
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools" / "personal-dashboard"))

from comms_response_clamp import clamp_response_hours_unix

TZ = ZoneInfo("America/Los_Angeles")
ARCHIVE_DB = Path.home() / ".config" / "ibx" / "archive_log.db"

LOG_PREFIX = "sync-ext"


# ── Archive log DB ───────────────────────────────────────────────────────────

def get_db():
    ARCHIVE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(ARCHIVE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS archive_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            item_uid TEXT NOT NULL,
            action TEXT NOT NULL,
            message_type TEXT NOT NULL,
            response_min REAL,
            UNIQUE(item_uid, action)
        )
    """)
    try:
        conn.execute("ALTER TABLE archive_log ADD COLUMN response_min REAL")
    except Exception:
        pass
    return conn


def insert_reply(conn, uid_str, epoch, msg_type, response_min=None):
    """INSERT OR IGNORE a reply row. Returns True if inserted (new)."""
    cursor = conn.execute(
        "INSERT OR IGNORE INTO archive_log (timestamp, item_uid, action, message_type, response_min) VALUES (?, ?, 'reply', ?, ?)",
        (epoch, uid_str, msg_type, response_min),
    )
    return cursor.rowcount > 0


# ── Slack sync ───────────────────────────────────────────────────────────────

def slack_get(token, method, **params):
    url = f"https://slack.com/api/{method}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            if not data.get("ok"):
                return None
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After", 2 ** attempt)))
                continue
            return None
    return None


def sync_slack(conn, lookback_hours):
    tokens_file = Path.home() / ".config" / "slack" / "tokens.json"
    if not tokens_file.exists():
        return 0
    tokens = json.loads(tokens_file.read_text())
    cutoff = time.time() - lookback_hours * 3600
    inserted = 0

    for workspace, token in tokens.items():
        # Get self ID
        auth = slack_get(token, "auth.test")
        if not auth:
            continue
        self_id = auth.get("user_id")

        # List recent DM/MPIM channels
        channels_data = slack_get(token, "conversations.list",
                                  types="im,mpim", limit=50,
                                  exclude_archived="true")
        if not channels_data:
            continue

        for ch in channels_data.get("channels", []):
            ch_id = ch["id"]
            # Fetch recent messages
            hist = slack_get(token, "conversations.history",
                             channel=ch_id, limit=30,
                             oldest=str(cutoff))
            if not hist:
                continue
            msgs = hist.get("messages", [])
            if not msgs:
                continue

            # Find sent messages and compute response time
            for i, m in enumerate(msgs):
                if m.get("user") != self_id:
                    continue
                if m.get("subtype"):
                    continue
                sent_ts = float(m.get("ts", 0))
                if sent_ts < cutoff:
                    continue

                # Find most recent inbound message before this one
                # (msgs are newest-first from API)
                recv_ts = 0.0
                for prev in msgs[i + 1:]:
                    if prev.get("user") != self_id and not prev.get("subtype"):
                        recv_ts = float(prev.get("ts", 0))
                        break

                resp_min = None
                if recv_ts > 0:
                    resp_min = clamp_response_hours_unix(sent_ts, recv_ts) * 60.0

                uid = f"slack_sent:{ch_id}:{m['ts']}"
                if insert_reply(conn, uid, sent_ts, "slack", resp_min):
                    inserted += 1

    conn.commit()
    return inserted


# ── Gmail sync ───────────────────────────────────────────────────────────────

def sync_gmail(conn, lookback_hours):
    try:
        from ibx import get_gmail_service, ACCOUNTS, CONFIG_DIR
    except ImportError:
        return 0

    cutoff_date = (datetime.now() - timedelta(hours=lookback_hours)).strftime("%Y/%m/%d")
    inserted = 0

    for acct in ACCOUNTS:
        try:
            svc = get_gmail_service(acct["tokens"], acct["creds"])
        except Exception:
            continue

        try:
            results = svc.users().messages().list(
                userId="me",
                q=f"in:sent after:{cutoff_date}",
                maxResults=50,
            ).execute()
        except Exception:
            continue

        msg_ids = [m["id"] for m in results.get("messages", [])]

        for msg_id in msg_ids:
            uid = f"gmail_sent:{msg_id}"
            # Quick dedup check before fetching
            existing = conn.execute(
                "SELECT 1 FROM archive_log WHERE item_uid = ? AND action = 'reply'", (uid,)
            ).fetchone()
            if existing:
                continue

            try:
                msg = svc.users().messages().get(
                    userId="me", id=msg_id, format="metadata",
                    metadataHeaders=["Date", "In-Reply-To", "Subject"],
                ).execute()
            except Exception:
                continue

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

            # Only count replies (has In-Reply-To header)
            if "In-Reply-To" not in headers:
                continue

            # Parse sent time
            sent_epoch = float(msg.get("internalDate", 0)) / 1000.0
            if sent_epoch < time.time() - lookback_hours * 3600:
                continue

            # Try to find the original message for response time
            resp_min = None
            in_reply_to = headers.get("In-Reply-To", "")
            if in_reply_to:
                try:
                    orig_results = svc.users().messages().list(
                        userId="me",
                        q=f"rfc822msgid:{in_reply_to}",
                        maxResults=1,
                    ).execute()
                    orig_msgs = orig_results.get("messages", [])
                    if orig_msgs:
                        orig = svc.users().messages().get(
                            userId="me", id=orig_msgs[0]["id"], format="minimal",
                        ).execute()
                        recv_epoch = float(orig.get("internalDate", 0)) / 1000.0
                        if recv_epoch > 0:
                            resp_min = clamp_response_hours_unix(sent_epoch, recv_epoch) * 60.0
                except Exception:
                    pass

            if insert_reply(conn, uid, sent_epoch, "email", resp_min):
                inserted += 1

        conn.commit()
    return inserted


# ── Outlook/Teams cross-sync ────────────────────────────────────────────────

def sync_from_response_db(conn, db_path, msg_type, table_name, id_prefix):
    """Import rows from an existing response_times.db into archive_log."""
    if not db_path.exists():
        return 0
    inserted = 0
    try:
        src = sqlite3.connect(str(db_path))
        src.row_factory = sqlite3.Row
        rows = src.execute(f"SELECT * FROM {table_name}").fetchall()
        for r in rows:
            try:
                action_at = r["action_at"]
                if not action_at:
                    continue
                dt = datetime.fromisoformat(action_at.replace("Z", "+00:00"))
                epoch = dt.timestamp()
                resp_hours = r["response_hours"]
                resp_min = resp_hours * 60 if resp_hours is not None else None
                uid = f"{id_prefix}:{r['item_id']}"
                action = "reply"
                if insert_reply(conn, uid, epoch, msg_type, resp_min):
                    inserted += 1
            except Exception:
                continue
        src.close()
        conn.commit()
    except Exception:
        pass
    return inserted


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback-hours", type=int, default=24)
    args = parser.parse_args()

    conn = get_db()
    results = {}

    # Slack
    n = sync_slack(conn, args.lookback_hours)
    results["slack"] = n
    print(f"[{LOG_PREFIX}] Slack: {n} new replies")

    # Gmail
    n = sync_gmail(conn, args.lookback_hours)
    results["gmail"] = n
    print(f"[{LOG_PREFIX}] Gmail: {n} new replies")

    # Outlook cross-sync
    n = sync_from_response_db(
        conn,
        Path.home() / ".config" / "outlook" / "response_times.db",
        "outlook", "outlook_responses", "outlook",
    )
    results["outlook"] = n
    print(f"[{LOG_PREFIX}] Outlook: {n} new replies")

    # Teams cross-sync
    n = sync_from_response_db(
        conn,
        Path.home() / ".config" / "teams" / "response_times.db",
        "teams", "teams_responses", "teams",
    )
    results["teams"] = n
    print(f"[{LOG_PREFIX}] Teams: {n} new replies")

    total = sum(results.values())
    print(f"[{LOG_PREFIX}] Total: {total} new replies synced")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
