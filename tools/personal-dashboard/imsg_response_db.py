#!/usr/bin/env python3
"""iMessage Response Pair Database.

Scans ~/Library/Messages/chat.db for response pairs (received → sent in same chat)
and stores them in a local SQLite DB for dashboard consumption.

Run periodically (e.g. every 30 min via cron) or on demand.

Response pair definition:
  A "response" is any sent message where there exists a received message
  in the same chat within the preceding 72 hours. The response time is
  the delta between the last received message and the sent message.

Storage: ~/vault/i447/i446/imsg-responses.db
"""

import sqlite3
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

from comms_response_clamp import clamp_response_hours_unix, MAX_RESPONSE_HOURS

CHATDB = Path.home() / "Library" / "Messages" / "chat.db"
RESPONSE_DB = Path.home() / "vault" / "i447" / "i446" / "imsg-responses.db"
APPLE_EPOCH = 978307200
LOOKBACK_DAYS = 30
# Original received-message matching window (a sent message only counts as a
# "response" if there's a received message in the same chat within this window).
# After matching, the recorded response_hours is clamped via comms_response_clamp
# so the daily timer reset gives us a max latency of 24h.
MATCH_WINDOW_HOURS = 72


def init_db():
    conn = sqlite3.connect(str(RESPONSE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS response_pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            chat_name TEXT,
            recv_time TEXT NOT NULL,       -- ISO 8601 local time
            sent_time TEXT NOT NULL,       -- ISO 8601 local time
            response_hours REAL NOT NULL,  -- delta in hours
            recv_preview TEXT,             -- first 100 chars of received msg
            sent_preview TEXT,             -- first 100 chars of sent msg
            day TEXT NOT NULL,             -- YYYY-MM-DD of sent message
            UNIQUE(chat_id, sent_time)     -- dedup on rescan
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            day TEXT NOT NULL,
            sent_count INTEGER DEFAULT 0,
            received_count INTEGER DEFAULT 0,
            response_count INTEGER DEFAULT 0,
            avg_response_hours REAL,
            median_response_hours REAL,
            response_count_daytime INTEGER DEFAULT 0,
            avg_response_hours_daytime REAL,
            median_response_hours_daytime REAL,
            PRIMARY KEY(day)
        )
    """)
    # Migrate older DBs that pre-date the daytime columns
    cols = {row[1] for row in conn.execute("PRAGMA table_info(daily_stats)")}
    for col, ddl in [
        ("response_count_daytime", "ALTER TABLE daily_stats ADD COLUMN response_count_daytime INTEGER DEFAULT 0"),
        ("avg_response_hours_daytime", "ALTER TABLE daily_stats ADD COLUMN avg_response_hours_daytime REAL"),
        ("median_response_hours_daytime", "ALTER TABLE daily_stats ADD COLUMN median_response_hours_daytime REAL"),
    ]:
        if col not in cols:
            conn.execute(ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pairs_day ON response_pairs(day)")
    conn.commit()
    return conn


def scan_chatdb(days=LOOKBACK_DAYS):
    """Read chat.db and return response pairs."""
    if not CHATDB.exists():
        print("chat.db not found")
        return [], {}

    src = sqlite3.connect(f"file:{CHATDB}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    cutoff_ns = (int(datetime.now(timezone.utc).timestamp()) - APPLE_EPOCH - days * 86400) * 1_000_000_000

    rows = src.execute("""
        SELECT
            cmj.chat_id,
            COALESCE(c.display_name, c.chat_identifier) as chat_name,
            m.is_from_me,
            COALESCE(m.text, '') as text,
            m.date / 1000000000 + ? as unix_ts,
            datetime(m.date / 1000000000 + ?, 'unixepoch', 'localtime') as local_time,
            date(m.date / 1000000000 + ?, 'unixepoch', 'localtime') as day_str
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        JOIN chat c ON c.ROWID = cmj.chat_id
        WHERE m.date > ?
          AND (m.text IS NOT NULL OR m.attributedBody IS NOT NULL)
          AND m.associated_message_type = 0
        ORDER BY cmj.chat_id, m.date
    """, (APPLE_EPOCH, APPLE_EPOCH, APPLE_EPOCH, cutoff_ns)).fetchall()

    src.close()

    # Group by chat, find response pairs
    from itertools import groupby
    pairs = []
    daily_counts = {}  # day -> {sent, received}

    for chat_id, msgs in groupby(rows, key=lambda r: r["chat_id"]):
        msgs_list = list(msgs)

        for msg in msgs_list:
            day = msg["day_str"]
            if day not in daily_counts:
                daily_counts[day] = {"sent": 0, "received": 0}
            if msg["is_from_me"]:
                daily_counts[day]["sent"] += 1
            else:
                daily_counts[day]["received"] += 1

        for i, msg in enumerate(msgs_list):
            if not msg["is_from_me"]:
                continue
            # Only count the FIRST sent message after received messages.
            # Consecutive sent messages (follow-ups) are not new "responses."
            if i > 0 and msgs_list[i - 1]["is_from_me"]:
                continue
            # Find most recent received message before this sent
            for j in range(i - 1, -1, -1):
                if not msgs_list[j]["is_from_me"]:
                    raw_h = (msg["unix_ts"] - msgs_list[j]["unix_ts"]) / 3600
                    if 0 < raw_h <= MATCH_WINDOW_HOURS:
                        delta_h = clamp_response_hours_unix(
                            msg["unix_ts"], msgs_list[j]["unix_ts"]
                        )
                        pairs.append({
                            "chat_id": chat_id,
                            "chat_name": msg["chat_name"],
                            "recv_time": msgs_list[j]["local_time"],
                            "sent_time": msg["local_time"],
                            "response_hours": round(delta_h, 2),
                            "recv_preview": (msgs_list[j]["text"] or "")[:100],
                            "sent_preview": (msg["text"] or "")[:100],
                            "day": msg["day_str"],
                        })
                    break

    return pairs, daily_counts


def update_db(pairs, daily_counts):
    conn = init_db()

    # Upsert pairs
    for p in pairs:
        conn.execute("""
            INSERT INTO response_pairs (chat_id, chat_name, recv_time, sent_time,
                                        response_hours, recv_preview, sent_preview, day)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, sent_time) DO UPDATE SET
                response_hours = excluded.response_hours,
                recv_preview = excluded.recv_preview,
                sent_preview = excluded.sent_preview
        """, (p["chat_id"], p["chat_name"], p["recv_time"], p["sent_time"],
              p["response_hours"], p["recv_preview"], p["sent_preview"], p["day"]))

    # Daytime: messages received between 6am and 9pm local
    DAY_START, DAY_END = 6, 21

    def _recv_hour(p):
        try:
            return datetime.fromisoformat(p["recv_time"]).hour
        except Exception:
            return None

    # Upsert daily stats
    for day, counts in daily_counts.items():
        # Get response stats for this day
        day_pairs = [p for p in pairs if p["day"] == day]
        resp_count = len(day_pairs)
        avg_h = round(sum(p["response_hours"] for p in day_pairs) / resp_count, 2) if resp_count else None
        hours_sorted = sorted(p["response_hours"] for p in day_pairs)
        median_h = round(hours_sorted[len(hours_sorted) // 2], 2) if hours_sorted else None

        day_pairs_dt = [p for p in day_pairs if (h := _recv_hour(p)) is not None and DAY_START <= h < DAY_END]
        resp_count_dt = len(day_pairs_dt)
        avg_h_dt = round(sum(p["response_hours"] for p in day_pairs_dt) / resp_count_dt, 2) if resp_count_dt else None
        hours_sorted_dt = sorted(p["response_hours"] for p in day_pairs_dt)
        median_h_dt = round(hours_sorted_dt[len(hours_sorted_dt) // 2], 2) if hours_sorted_dt else None

        conn.execute("""
            INSERT INTO daily_stats (day, sent_count, received_count, response_count,
                                     avg_response_hours, median_response_hours,
                                     response_count_daytime, avg_response_hours_daytime,
                                     median_response_hours_daytime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                sent_count = excluded.sent_count,
                received_count = excluded.received_count,
                response_count = excluded.response_count,
                avg_response_hours = excluded.avg_response_hours,
                median_response_hours = excluded.median_response_hours,
                response_count_daytime = excluded.response_count_daytime,
                avg_response_hours_daytime = excluded.avg_response_hours_daytime,
                median_response_hours_daytime = excluded.median_response_hours_daytime
        """, (day, counts["sent"], counts["received"], resp_count, avg_h, median_h,
              resp_count_dt, avg_h_dt, median_h_dt))

    conn.commit()

    # Report
    total_pairs = conn.execute("SELECT COUNT(*) FROM response_pairs").fetchone()[0]
    total_days = conn.execute("SELECT COUNT(*) FROM daily_stats").fetchone()[0]
    conn.close()

    return total_pairs, total_days


def main():
    print("Scanning chat.db...")
    pairs, daily_counts = scan_chatdb()
    print(f"Found {len(pairs)} response pairs across {len(daily_counts)} days")

    total_pairs, total_days = update_db(pairs, daily_counts)
    print(f"DB updated: {total_pairs} total pairs, {total_days} days tracked")

    # Show recent
    if pairs:
        recent = sorted(pairs, key=lambda p: p["sent_time"], reverse=True)[:5]
        print("\nRecent responses:")
        for p in recent:
            print(f"  {p['sent_time']} → {p['chat_name']}: {p['response_hours']:.1f}h ({p['sent_preview'][:40]})")


if __name__ == "__main__":
    main()
