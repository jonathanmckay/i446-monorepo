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
    "m5c7": "m5x2",
    "gmail": "personal",
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

    work_all = [r["hours"] for r in all_data if r["account"] == "m5x2"]
    work_7 = [r["hours"] for r in all_data if r["account"] == "m5x2"
              and date.fromisoformat(r["date"]) > cutoff_7]
    work_28 = [r["hours"] for r in all_data if r["account"] == "m5x2"
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
