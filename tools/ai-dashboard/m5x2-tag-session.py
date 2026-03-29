#!/usr/bin/env python3
"""
Tag LLM sessions with user_id and property_code

Usage:
  # Tag latest session
  python3 m5x2-tag-session.py --user jm --property r202

  # Tag specific session
  python3 m5x2-tag-session.py --session claude-abc123 --user lx --property m221

  # Tag all untagged sessions for a user
  python3 m5x2-tag-session.py --user jm --property portfolio --all-untagged
"""

import argparse
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path.home() / "vault" / "i447" / "i446" / "llm-sessions.db"


def tag_session(session_id=None, user_id=None, property_code=None, all_untagged=False):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if all_untagged and user_id:
        # Tag all untagged sessions for this user
        cursor.execute("""
            UPDATE sessions
            SET user_id = ?, property_code = ?
            WHERE user_id = 'jm' AND property_code IS NULL
        """, (user_id, property_code))
        rows_updated = cursor.rowcount
        print(f"✓ Tagged {rows_updated} untagged sessions for {user_id}")

    elif session_id:
        # Tag specific session
        cursor.execute("""
            UPDATE sessions
            SET user_id = ?, property_code = ?
            WHERE session_id = ?
        """, (user_id, property_code, session_id))
        if cursor.rowcount > 0:
            print(f"✓ Tagged session {session_id}")
        else:
            print(f"✗ Session {session_id} not found")

    else:
        # Tag latest session
        cursor.execute("""
            UPDATE sessions
            SET user_id = ?, property_code = ?
            WHERE session_id = (
                SELECT session_id FROM sessions
                ORDER BY start_time DESC
                LIMIT 1
            )
        """, (user_id, property_code))

        if cursor.rowcount > 0:
            # Get the session info
            cursor.execute("""
                SELECT session_id, start_time, context
                FROM sessions
                ORDER BY start_time DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            print(f"✓ Tagged latest session:")
            print(f"  Session: {row[0]}")
            print(f"  Time: {row[1]}")
            print(f"  Context: {row[2][:60]}...")
        else:
            print("✗ No sessions found")

    conn.commit()
    conn.close()


def list_recent_sessions(limit=10):
    """List recent sessions for review"""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cursor = conn.cursor()

    rows = cursor.execute("""
        SELECT session_id, start_time, user_id, property_code, provider, context
        FROM sessions
        ORDER BY start_time DESC
        LIMIT ?
    """, (limit,)).fetchall()

    print(f"\n📋 Last {limit} sessions:")
    print(f"{'Time':<20} {'User':<6} {'Property':<12} {'Provider':<10} {'Context':<40}")
    print("-" * 100)
    for row in rows:
        time = row[1][:19] if row[1] else ""
        user = row[2] or "?"
        prop = row[3] or "untagged"
        provider = row[4]
        context = (row[5] or "")[:40]
        print(f"{time:<20} {user:<6} {prop:<12} {provider:<10} {context:<40}")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tag LLM sessions with user and property")
    parser.add_argument("--user", "-u", help="User ID (jm, lx, etc.)")
    parser.add_argument("--property", "-p", help="Property code (r202, m221, etc.)")
    parser.add_argument("--session", "-s", help="Specific session ID to tag")
    parser.add_argument("--all-untagged", "-a", action="store_true", help="Tag all untagged sessions")
    parser.add_argument("--list", "-l", action="store_true", help="List recent sessions")
    parser.add_argument("--limit", type=int, default=10, help="Number of sessions to list")

    args = parser.parse_args()

    if args.list:
        list_recent_sessions(args.limit)
    elif args.user or args.property or args.session:
        tag_session(
            session_id=args.session,
            user_id=args.user,
            property_code=args.property,
            all_untagged=args.all_untagged
        )
    else:
        parser.print_help()
