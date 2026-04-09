#!/usr/bin/env python3
"""
gen_email_stats.py — Generate email response time stats and push to GitHub Gist.

For each sent reply in the last 30 days, finds the original received message
in the same thread and computes the response delta in hours.

Usage:
    python3 gen_email_stats.py

Pushes to gist: 7c08fd1a83c8f3bbab3917bdb3d33df1
"""

import json
import os
import subprocess
import sys
import warnings
warnings.filterwarnings("ignore")

from collections import defaultdict
from datetime import datetime, timedelta, date, timezone
from pathlib import Path
import statistics
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).parent.parent / "ibx"))
from ibx import get_gmail_service, ACCOUNTS

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Los_Angeles")
GIST_ID = "7c08fd1a83c8f3bbab3917bdb3d33df1"
DAYS = 30

ACCOUNT_DISPLAY = {
    "m5c7": "m5x2 gmail",
    "gmail": "s897 gmail",
}


def get_github_token():
    gh = os.environ.get("GH_PATH", "/opt/homebrew/bin/gh")
    result = subprocess.run([gh, "auth", "token"], capture_output=True, text=True)
    return result.stdout.strip()


def get_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def get_thread_messages(service, thread_id):
    """Fetch all messages in a thread, return list sorted by internalDate asc."""
    result = service.users().threads().get(
        userId="me", id=thread_id, format="metadata",
        metadataHeaders=["From", "To", "Cc", "Subject", "Date", "Message-ID", "In-Reply-To"]
    ).execute()
    msgs = result.get("messages", [])
    msgs.sort(key=lambda m: int(m.get("internalDate", 0)))
    return msgs


def is_sent_by_me(msg, my_email):
    """True if the message was sent by me (appears in SENT label)."""
    labels = msg.get("labelIds", [])
    return "SENT" in labels


def compute_response_times(service, account_name, days=DAYS):
    """
    For each sent reply in the last `days` days, find the preceding received message
    in the same thread and compute the response time in hours.

    Returns list of {"date": "YYYY-MM-DD", "hours": float} dicts.
    """
    profile = service.users().getProfile(userId="me").execute()
    my_email = profile["emailAddress"].lower()

    since_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    since_date = (date.today() - timedelta(days=days)).strftime("%Y/%m/%d")

    # Fetch sent messages in the last N days
    results = []
    page_token = None
    while True:
        kwargs = {
            "userId": "me",
            "q": f"in:sent after:{since_date}",
            "maxResults": 200,
            "fields": "messages(id,threadId),nextPageToken",
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = service.users().messages().list(**kwargs).execute()
        results.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    print(f"  {account_name}: {len(results)} sent messages in last {days}d")

    response_times = []
    seen_threads = set()

    for msg_stub in results:
        thread_id = msg_stub["threadId"]
        if thread_id in seen_threads:
            continue
        seen_threads.add(thread_id)

        try:
            thread_msgs = get_thread_messages(service, thread_id)
        except Exception:
            continue

        # Walk pairs: find sent messages that follow a received message
        for i, msg in enumerate(thread_msgs):
            if not is_sent_by_me(msg, my_email):
                continue

            sent_ts = int(msg.get("internalDate", 0))
            if sent_ts < since_ts:
                continue

            # Find the most recent received message before this sent one
            received_msg = None
            for j in range(i - 1, -1, -1):
                if not is_sent_by_me(thread_msgs[j], my_email):
                    received_msg = thread_msgs[j]
                    break

            if received_msg is None:
                continue  # Original send, not a reply

            received_ts = int(received_msg.get("internalDate", 0))
            if received_ts <= 0 or sent_ts <= received_ts:
                continue

            delta_hours = (sent_ts - received_ts) / 3_600_000  # ms → hours

            # Cap at 72h to exclude abandoned threads
            if delta_hours > 72:
                continue

            sent_dt = datetime.fromtimestamp(sent_ts / 1000, tz=LOCAL_TZ)
            day_str = sent_dt.date().isoformat()
            response_times.append({"date": day_str, "hours": round(delta_hours, 2)})

    return response_times


def build_stats(all_data):
    """
    all_data: list of {"date", "hours", "account"} dicts
    Returns:
        daily: list of {"date", "account", "avg_hours", "count"}
        summary: stats over work account
    """
    by_day_acct = defaultdict(list)
    for rec in all_data:
        by_day_acct[(rec["date"], rec["account"])].append(rec["hours"])

    daily = []
    for (day, acct), hours_list in sorted(by_day_acct.items()):
        avg = round(sum(hours_list) / len(hours_list), 2)
        daily.append({"date": day, "account": acct, "avg_hours": avg, "count": len(hours_list)})

    # Summary over work account, last 7d and 28d
    today = date.today()
    cutoff_7 = today - timedelta(days=7)
    cutoff_28 = today - timedelta(days=28)

    work_all = [r["hours"] for r in all_data if r["account"] == "m5x2 gmail"]
    work_7 = [r["hours"] for r in all_data if r["account"] == "m5x2 gmail"
              and date.fromisoformat(r["date"]) > cutoff_7]
    work_28 = [r["hours"] for r in all_data if r["account"] == "m5x2 gmail"
               and date.fromisoformat(r["date"]) > cutoff_28]

    def safe_mean(lst):
        return round(sum(lst) / len(lst), 2) if lst else None

    def safe_median(lst):
        return round(statistics.median(lst), 2) if lst else None

    summary = {
        "work_last_7d_avg_hours": safe_mean(work_7),
        "work_last_7d_median_hours": safe_median(work_7),
        "work_last_28d_avg_hours": safe_mean(work_28),
        "work_last_28d_median_hours": safe_median(work_28),
        "work_sample_count": len(work_all),
    }

    return daily, summary


def push_to_gist(payload, token):
    data = json.dumps({
        "files": {
            "email_response_stats.json": {
                "content": json.dumps(payload, indent=2)
            }
        }
    }).encode()

    req = urllib.request.Request(
        f"https://api.github.com/gists/{GIST_ID}",
        data=data,
        method="PATCH",
    )
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


def compute_imessage_response_times(days=DAYS):
    """
    Query the local iMessage database for response times.
    For each sent message, find the preceding received message in the same chat
    and compute the response delta in hours. Cap at 72h.

    Returns list of {"date": "YYYY-MM-DD", "hours": float} dicts.
    """
    import sqlite3 as _sqlite3

    db_path = Path.home() / "Library" / "Messages" / "chat.db"
    if not db_path.exists():
        print("  WARN: iMessage database not found")
        return []

    conn = _sqlite3.connect(str(db_path))
    conn.row_factory = _sqlite3.Row

    # Apple epoch: seconds since 2001-01-01 = unix 978307200
    # message.date is in nanoseconds since Apple epoch
    apple_epoch_offset = 978307200
    cutoff_ns = (int(datetime.now(timezone.utc).timestamp()) - apple_epoch_offset - days * 86400) * 1_000_000_000

    # Get all messages in the window, grouped by chat, ordered by date
    rows = conn.execute("""
        SELECT
            cmj.chat_id,
            m.is_from_me,
            m.date / 1000000000 + ? as unix_ts,
            date(m.date / 1000000000 + ?, 'unixepoch', 'localtime') as day_str
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        WHERE m.date > ?
          AND (m.text IS NOT NULL OR m.attributedBody IS NOT NULL)
          AND m.associated_message_type = 0
        ORDER BY cmj.chat_id, m.date
    """, (apple_epoch_offset, apple_epoch_offset, cutoff_ns)).fetchall()

    conn.close()

    # Group by chat_id, then walk pairs
    from itertools import groupby
    response_times = []
    for chat_id, msgs in groupby(rows, key=lambda r: r["chat_id"]):
        msgs_list = list(msgs)
        for i, msg in enumerate(msgs_list):
            if not msg["is_from_me"]:
                continue
            # Find preceding received message
            for j in range(i - 1, -1, -1):
                if not msgs_list[j]["is_from_me"]:
                    delta_hours = (msg["unix_ts"] - msgs_list[j]["unix_ts"]) / 3600
                    if 0 < delta_hours <= 72:
                        response_times.append({
                            "date": msg["day_str"],
                            "hours": round(delta_hours, 2),
                        })
                    break

    return response_times


SLACK_CONFIG = Path.home() / ".config" / "slack" / "tokens.json"


def compute_slack_response_times(days=DAYS):
    """
    Compute Slack DM response times across all configured workspaces.
    For each sent reply in the last `days` days, find the preceding received message
    in the same DM conversation and compute the response delta in hours. Cap at 72h.

    Returns list of {"date": "YYYY-MM-DD", "hours": float} dicts.
    """
    if not SLACK_CONFIG.exists():
        print("  WARN: Slack tokens file not found")
        return []

    tokens = json.loads(SLACK_CONFIG.read_text())
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ts = str(cutoff.timestamp())
    response_times = []

    for workspace, token in tokens.items():
        try:
            # Get our user ID
            req = urllib.request.Request(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                auth = json.loads(r.read())
            if not auth.get("ok"):
                continue
            self_id = auth["user_id"]

            # List DM conversations
            cursor = None
            dm_channels = []
            while True:
                params = {"types": "im", "limit": "200"}
                if cursor:
                    params["cursor"] = cursor
                url = "https://slack.com/api/conversations.list?" + urllib.parse.urlencode(params)
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = json.loads(r.read())
                if not data.get("ok"):
                    break
                dm_channels.extend(data.get("channels", []))
                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

            # For each DM, fetch recent history and compute response pairs
            for ch in dm_channels:
                ch_id = ch["id"]
                try:
                    url = "https://slack.com/api/conversations.history?" + urllib.parse.urlencode({
                        "channel": ch_id, "limit": "200", "oldest": cutoff_ts,
                    })
                    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
                    with urllib.request.urlopen(req, timeout=15) as r:
                        hist = json.loads(r.read())
                    if not hist.get("ok"):
                        continue
                    msgs = sorted(hist.get("messages", []), key=lambda m: float(m.get("ts", 0)))
                except Exception:
                    continue

                for i, msg in enumerate(msgs):
                    if msg.get("user") != self_id:
                        continue
                    # Find preceding message from someone else
                    for j in range(i - 1, -1, -1):
                        if msgs[j].get("user") != self_id:
                            sent_ts = float(msg["ts"])
                            recv_ts = float(msgs[j]["ts"])
                            delta_hours = (sent_ts - recv_ts) / 3600
                            if 0 < delta_hours <= 72:
                                sent_dt = datetime.fromtimestamp(sent_ts, tz=LOCAL_TZ)
                                response_times.append({
                                    "date": sent_dt.date().isoformat(),
                                    "hours": round(delta_hours, 2),
                                })
                            break

            print(f"  {workspace}: {len([r for r in response_times])} reply events so far")
        except Exception as e:
            print(f"  WARN: Slack {workspace} failed: {e}")

    return response_times


def main():
    token = get_github_token()
    if not token:
        print("ERROR: gh auth token returned empty. Run: gh auth login")
        sys.exit(1)

    all_data = []
    for acct in ACCOUNTS:
        display_name = ACCOUNT_DISPLAY.get(acct["name"], acct["name"])
        print(f"Fetching {acct['name']} ({display_name})...")
        try:
            service = get_gmail_service(acct["tokens"], acct["creds"])
            times = compute_response_times(service, acct["name"])
            for rec in times:
                rec["account"] = display_name
            all_data.extend(times)
            print(f"  → {len(times)} reply events")
        except Exception as e:
            print(f"  WARN: {acct['name']} failed: {e}")

    # iMessage response times
    print("Fetching iMessage...")
    try:
        imsg_times = compute_imessage_response_times()
        for rec in imsg_times:
            rec["account"] = "imessage"
        all_data.extend(imsg_times)
        print(f"  → {len(imsg_times)} reply events")
    except Exception as e:
        print(f"  WARN: iMessage failed: {e}")

    # Slack response times
    print("Fetching Slack...")
    try:
        slack_times = compute_slack_response_times()
        for rec in slack_times:
            rec["account"] = "slack"
        all_data.extend(slack_times)
        print(f"  → {len(slack_times)} reply events")
    except Exception as e:
        print(f"  WARN: Slack failed: {e}")

    daily, summary = build_stats(all_data)
    print(f"\nBuilt {len(daily)} daily entries across {len(all_data)} reply events")
    print(f"Summary: {summary}")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "daily": daily,
        "summary": summary,
    }

    status = push_to_gist(payload, token)
    print(f"\nGist updated (HTTP {status}): {GIST_ID}")


if __name__ == "__main__":
    main()
