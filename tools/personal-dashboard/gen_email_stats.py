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

# Daily-reset clamp for individual response timers (max 24h, resets at PST midnight)
sys.path.insert(0, str(Path(__file__).parent))
from comms_response_clamp import clamp_response_hours_unix

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

    Returns (response_times, sent_by_day) where:
        response_times: list of {"date": "YYYY-MM-DD", "hours": float}
        sent_by_day: dict of {"YYYY-MM-DD": int} total sent messages per day
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
    sent_by_day = defaultdict(int)
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

        # Classify each sent message as reply or proactive
        for i, msg in enumerate(thread_msgs):
            if not is_sent_by_me(msg, my_email):
                continue

            sent_ts = int(msg.get("internalDate", 0))
            if sent_ts < since_ts:
                continue

            sent_dt = datetime.fromtimestamp(sent_ts / 1000, tz=LOCAL_TZ)
            day_str = sent_dt.date().isoformat()
            sent_by_day[day_str] += 1

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

            raw_hours = (sent_ts - received_ts) / 3_600_000  # ms → hours

            # Skip threads that have been abandoned for more than 72h
            # (matching window unchanged); response_hours itself resets daily.
            if raw_hours > 72:
                continue

            delta_hours = clamp_response_hours_unix(sent_ts / 1000, received_ts / 1000)

            recv_hour = datetime.fromtimestamp(received_ts / 1000, tz=LOCAL_TZ).hour
            response_times.append({"date": day_str, "hours": round(delta_hours, 2), "recv_hour": recv_hour})

    return response_times, dict(sent_by_day)


def build_stats(all_data, sent_counts=None):
    """
    all_data: list of {"date", "hours", "account"} dicts
    sent_counts: dict of {account: {date: int}} total sent messages per day
    Returns:
        daily: list of {"date", "account", "avg_hours", "count", "sent_count"}
        summary: stats over work account
    """
    # Daytime = inbound message received between 6am and 9pm local
    DAY_START, DAY_END = 6, 21

    sent_counts = sent_counts or {}
    by_day_acct = defaultdict(list)
    by_day_acct_daytime = defaultdict(list)
    for rec in all_data:
        key = (rec["date"], rec["account"])
        by_day_acct[key].append(rec["hours"])
        rh = rec.get("recv_hour")
        if rh is not None and DAY_START <= rh < DAY_END:
            by_day_acct_daytime[key].append(rec["hours"])

    # Collect all (date, account) keys that have sent_counts but no reply data
    all_keys = set(by_day_acct.keys())
    for acct, day_map in sent_counts.items():
        for day, count in day_map.items():
            all_keys.add((day, acct))

    # Always emit a row for *today* (local) for every account that has any
    # activity in the last 7 days. This prevents a Graph API timeout from
    # silently dropping today's bar from the dashboard — the user must see "0"
    # rather than "missing", because "missing" looks identical to "no outage".
    today_iso = date.today().isoformat()
    recent_cutoff = (date.today() - timedelta(days=7))
    recent_accounts = {acct for (d, acct) in by_day_acct.keys()
                       if date.fromisoformat(d) >= recent_cutoff}
    recent_accounts |= set(sent_counts.keys())
    for acct in recent_accounts:
        all_keys.add((today_iso, acct))

    daily = []
    for day, acct in sorted(all_keys):
        hours_list = by_day_acct.get((day, acct), [])
        daytime_list = by_day_acct_daytime.get((day, acct), [])
        reply_count = len(hours_list)
        avg = round(sum(hours_list) / reply_count, 2) if reply_count else None
        avg_daytime = round(sum(daytime_list) / len(daytime_list), 2) if daytime_list else None
        sent = sent_counts.get(acct, {}).get(day, reply_count)
        daily.append({"date": day, "account": acct, "avg_hours": avg, "avg_hours_daytime": avg_daytime, "count": reply_count, "count_daytime": len(daytime_list), "sent_count": sent})

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

    Returns (response_times, sent_by_day) where:
        response_times: list of {"date": "YYYY-MM-DD", "hours": float}
        sent_by_day: dict of {"YYYY-MM-DD": int} total sent messages per day
    """
    import sqlite3 as _sqlite3

    db_path = Path.home() / "Library" / "Messages" / "chat.db"
    if not db_path.exists():
        print("  WARN: iMessage database not found")
        return [], {}

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
    sent_by_day = defaultdict(int)
    for chat_id, msgs in groupby(rows, key=lambda r: r["chat_id"]):
        msgs_list = list(msgs)
        for i, msg in enumerate(msgs_list):
            if not msg["is_from_me"]:
                continue
            sent_by_day[msg["day_str"]] += 1
            # Find preceding received message
            for j in range(i - 1, -1, -1):
                if not msgs_list[j]["is_from_me"]:
                    raw_hours = (msg["unix_ts"] - msgs_list[j]["unix_ts"]) / 3600
                    if 0 < raw_hours <= 72:
                        delta_hours = clamp_response_hours_unix(
                            msg["unix_ts"], msgs_list[j]["unix_ts"]
                        )
                        recv_hour = datetime.fromtimestamp(msgs_list[j]["unix_ts"], tz=LOCAL_TZ).hour
                        response_times.append({
                            "date": msg["day_str"],
                            "hours": round(delta_hours, 2),
                            "recv_hour": recv_hour,
                        })
                    break

    return response_times, dict(sent_by_day)


SLACK_CONFIG = Path.home() / ".config" / "slack" / "tokens.json"


def compute_slack_response_times(days=DAYS):
    """
    Compute Slack DM response times across all configured workspaces.
    For each sent reply in the last `days` days, find the preceding received message
    in the same DM conversation and compute the response delta in hours. Cap at 72h.

    Returns (response_times, sent_by_day) where:
        response_times: list of {"date": "YYYY-MM-DD", "hours": float}
        sent_by_day: dict of {"YYYY-MM-DD": int} total sent messages per day
    """
    if not SLACK_CONFIG.exists():
        print("  WARN: Slack tokens file not found")
        return [], {}

    tokens = json.loads(SLACK_CONFIG.read_text())
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ts = str(cutoff.timestamp())
    response_times = []
    sent_by_day = defaultdict(int)

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
                params = {"types": "im,mpim", "limit": "200"}
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
                    sent_ts = float(msg["ts"])
                    sent_dt = datetime.fromtimestamp(sent_ts, tz=LOCAL_TZ)
                    sent_by_day[sent_dt.date().isoformat()] += 1
                    # Find preceding message from someone else
                    for j in range(i - 1, -1, -1):
                        if msgs[j].get("user") != self_id:
                            recv_ts = float(msgs[j]["ts"])
                            delta_hours = (sent_ts - recv_ts) / 3600
                            if 0 < delta_hours <= 72:
                                recv_hour = datetime.fromtimestamp(recv_ts, tz=LOCAL_TZ).hour
                                response_times.append({
                                    "date": sent_dt.date().isoformat(),
                                    "hours": round(delta_hours, 2),
                                    "recv_hour": recv_hour,
                                })
                            break

            print(f"  {workspace}: {len([r for r in response_times])} reply events so far")
        except Exception as e:
            print(f"  WARN: Slack {workspace} failed: {e}")

    return response_times, dict(sent_by_day)


def parse_teams_replies(raw, days=DAYS, now=None):
    """Parse a Graph Teams search result into reply events.

    Reply detection: within each chat, sort messages chronologically; whenever
    one of MY messages is preceded by a message from someone else within ≤72h,
    emit a reply event (hours = my_sent - their_sent).

    This catches replies sent from the Teams app directly (which the local
    response_times.db never sees, because ibx only records actions taken
    through ibx itself).

    Returns list of {"date", "hours", "recv_hour"} dicts (LOCAL_TZ days).
    """
    if not raw:
        return []

    now = now or datetime.now(timezone.utc)
    today = now.astimezone(LOCAL_TZ).date()
    cutoff = today - timedelta(days=days)

    try:
        import teams_agency
        my_addresses = teams_agency.MY_ADDRESSES
        outer = json.loads(raw)
        inner = json.loads(outer.get("rawResponse", "{}"))
        hits_containers = inner.get("value", [{}])[0].get("hitsContainers", [{}])
        hits = hits_containers[0].get("hits", []) if hits_containers else []
    except Exception:
        return []

    # Group messages by chatId
    by_chat = defaultdict(list)
    for hit in hits:
        resource = hit.get("resource", {})
        chat_id = resource.get("chatId", "")
        if not chat_id:
            continue
        # Skip channel posts — only DMs / group chats count for response time
        channel_id = (resource.get("channelIdentity") or {}).get("channelId", "")
        if channel_id and "@thread.tacv2" in channel_id:
            continue
        sender_email = (resource.get("from", {}).get("emailAddress", {}).get("address") or "").lower()
        created = resource.get("createdDateTime", "")
        if not created:
            continue
        try:
            ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        is_mine = sender_email in my_addresses
        by_chat[chat_id].append((ts, is_mine))

    replies = []
    for chat_id, msgs in by_chat.items():
        msgs.sort(key=lambda m: m[0])
        # Find last "their" message before each "mine" message
        last_their_ts = None
        for ts, is_mine in msgs:
            if not is_mine:
                last_their_ts = ts
                continue
            if last_their_ts is None:
                # Mine without a preceding inbound — not a reply
                continue
            delta = (ts - last_their_ts).total_seconds() / 3600.0
            # Skip threads abandoned >72h (matching window). The recorded
            # response_hours is then clamped to today (PST) via the daily
            # reset, so the max is 24h.
            if delta <= 0 or delta > 72:
                last_their_ts = None
                continue
            delta = clamp_response_hours_unix(ts.timestamp(), last_their_ts.timestamp())
            local_sent = ts.astimezone(LOCAL_TZ)
            local_recv = last_their_ts.astimezone(LOCAL_TZ)
            day = local_sent.date()
            if day <= cutoff or day > today:
                last_their_ts = None
                continue
            replies.append({
                "date": day.isoformat(),
                "hours": round(delta, 2),
                "recv_hour": local_recv.hour,
            })
            # Reset so we don't double-count: a single inbound triggers at most
            # one reply event (the first responsive message from me).
            last_their_ts = None

    return replies


def compute_teams_replies_via_graph(days=DAYS):
    """Detect Teams replies from the last N days via a single Graph search.

    Independent of the local response_times.db — captures replies sent from
    the Teams app directly. Uses the same shrink-on-timeout pattern as
    `compute_teams_sent_counts`.
    """
    try:
        import teams_agency
    except Exception:
        return []

    requested = max(1, min(days, 2))
    for recent_days in (requested, 1):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=recent_days)).strftime("%Y-%m-%d")
        raw = teams_agency._teams_call("SearchTeamMessagesQueryParameters", {
            "queryString": f"sent>={cutoff}",
            "size": 200,
        }, timeout=60)
        if raw is not None:
            return parse_teams_replies(raw, days=recent_days)
    return []


def compute_teams_response_times(days=DAYS):
    """
    Read Teams DM response times from the local tracking DB populated by ibx.
    Returns list of {"date": "YYYY-MM-DD", "hours": float} dicts.
    """
    import sqlite3 as _sqlite3

    db_path = Path.home() / ".config" / "teams" / "response_times.db"
    if not db_path.exists():
        print("  WARN: Teams response DB not found")
        return []

    conn = _sqlite3.connect(str(db_path))
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    rows = conn.execute("""
        SELECT action_at, response_hours
        FROM teams_responses
        WHERE action = 'reply'
          AND response_hours IS NOT NULL
          AND action_at >= ?
    """, (cutoff,)).fetchall()
    conn.close()

    response_times = []
    for action_at, hours in rows:
        try:
            sent_dt = datetime.fromisoformat(action_at.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
            recv_dt = sent_dt - timedelta(hours=hours)
            response_times.append({"date": sent_dt.date().isoformat(), "hours": round(hours, 2), "recv_hour": recv_dt.hour})
        except Exception:
            continue

    return response_times


def parse_teams_sent_counts(raw, days=DAYS, now=None):
    """Parse Graph search results into local-day counts of my sent Teams messages."""
    if not raw:
        return {}

    now = now or datetime.now(timezone.utc)
    today = now.astimezone(LOCAL_TZ).date()
    cutoff = today - timedelta(days=days)

    try:
        import teams_agency
        my_addresses = teams_agency.MY_ADDRESSES
        outer = json.loads(raw)
        inner = json.loads(outer.get("rawResponse", "{}"))
        hits_containers = inner.get("value", [{}])[0].get("hitsContainers", [{}])
        hits = hits_containers[0].get("hits", []) if hits_containers else []
    except Exception:
        return {}

    counts = defaultdict(int)
    for hit in hits:
        resource = hit.get("resource", {})
        sender = resource.get("from", {}).get("emailAddress", {})
        sender_email = (sender.get("address") or "").lower()
        if sender_email not in my_addresses:
            continue

        created = resource.get("createdDateTime", "")
        if not created:
            continue

        try:
            sent_dt = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
        except (ValueError, TypeError):
            continue

        day = sent_dt.date()
        if day <= cutoff or day > today:
            continue
        counts[day.isoformat()] += 1

    return dict(counts)


def compute_teams_sent_counts(days=DAYS):
    """Count recent actual sent Teams messages by local day via Graph search.

    Graph search for Teams is flaky and the full 30-day window times out in
    practice. We progressively shrink the window (2d → 1d → today only) so that
    at minimum *today's* count survives a partial outage. If everything fails,
    return an empty dict — `build_stats` will still emit a zero-row for today.
    """
    try:
        import teams_agency
    except Exception:
        return {}

    # Try progressively smaller windows on timeout so today's count is most
    # likely to come back even when the broader query is failing.
    requested = max(1, min(days, 2))
    for recent_days in (requested, 1):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=recent_days)).strftime("%Y-%m-%d")
        raw = teams_agency._teams_call("SearchTeamMessagesQueryParameters", {
            "queryString": f"sent>={cutoff}",
            "size": 100,
        }, timeout=60)
        counts = parse_teams_sent_counts(raw, days=recent_days)
        if counts or raw is not None:
            # Either we got data, or the call succeeded with zero hits.
            return counts
        # raw is None → call failed; try a shorter window.
    return {}


def compute_outlook_response_times(days=DAYS):
    """
    Read Outlook response times from the local tracking DB populated by ibx.
    Returns list of {"date": "YYYY-MM-DD", "hours": float} dicts.
    """
    import sqlite3 as _sqlite3

    db_path = Path.home() / ".config" / "outlook" / "response_times.db"
    if not db_path.exists():
        print("  WARN: Outlook response DB not found")
        return []

    conn = _sqlite3.connect(str(db_path))
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    rows = conn.execute("""
        SELECT action_at, response_hours
        FROM outlook_responses
        WHERE action IN ('reply', 'reply_all')
          AND response_hours IS NOT NULL
          AND action_at >= ?
    """, (cutoff,)).fetchall()
    conn.close()

    response_times = []
    for action_at, hours in rows:
        try:
            sent_dt = datetime.fromisoformat(action_at.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
            recv_dt = sent_dt - timedelta(hours=hours)
            response_times.append({"date": sent_dt.date().isoformat(), "hours": round(hours, 2), "recv_hour": recv_dt.hour})
        except Exception:
            continue

    return response_times


def compute_outlook_sent_counts(days=DAYS):
    """Count recent sent Outlook messages by local day via Graph API search."""
    try:
        import outlook_agency
    except Exception:
        return {}

    recent_days = min(days, 2)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=recent_days)).strftime("%Y-%m-%d")
    raw = outlook_agency._mail_call("SearchMessagesQueryParameters", {
        "queryString": f"sent>={cutoff} kind:email from:me",
        "top": 100,
    }, timeout=60)
    if not raw:
        return {}

    now = datetime.now(timezone.utc)
    today = now.astimezone(LOCAL_TZ).date()
    cutoff_date = today - timedelta(days=recent_days)

    counts = defaultdict(int)
    try:
        outer = json.loads(raw)
        inner = json.loads(outer.get("rawResponse", "{}"))
        hits_containers = inner.get("value", [{}])[0].get("hitsContainers", [{}])
        hits = hits_containers[0].get("hits", []) if hits_containers else []
    except Exception:
        return {}

    for hit in hits:
        resource = hit.get("resource", {})
        created = resource.get("sentDateTime", "") or resource.get("createdDateTime", "")
        if not created:
            continue
        try:
            sent_dt = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
        except (ValueError, TypeError):
            continue
        day = sent_dt.date()
        if day <= cutoff_date or day > today:
            continue
        counts[day.isoformat()] += 1

    return dict(counts)


def main():
    token = get_github_token()
    if not token:
        print("ERROR: gh auth token returned empty. Run: gh auth login")
        sys.exit(1)

    all_data = []
    all_sent_counts = {}  # {account_name: {date: int}}

    for acct in ACCOUNTS:
        display_name = ACCOUNT_DISPLAY.get(acct["name"], acct["name"])
        print(f"Fetching {acct['name']} ({display_name})...")
        try:
            service = get_gmail_service(acct["tokens"], acct["creds"])
            times, sent_by_day = compute_response_times(service, acct["name"])
            for rec in times:
                rec["account"] = display_name
            all_data.extend(times)
            all_sent_counts[display_name] = sent_by_day
            print(f"  → {len(times)} reply events, {sum(sent_by_day.values())} total sent")
        except Exception as e:
            print(f"  WARN: {acct['name']} failed: {e}")

    # iMessage response times
    print("Fetching iMessage...")
    try:
        imsg_times, imsg_sent = compute_imessage_response_times()
        for rec in imsg_times:
            rec["account"] = "imessage"
        all_data.extend(imsg_times)
        all_sent_counts["imessage"] = imsg_sent
        print(f"  → {len(imsg_times)} reply events, {sum(imsg_sent.values())} total sent")
    except Exception as e:
        print(f"  WARN: iMessage failed: {e}")

    # Slack response times
    print("Fetching Slack...")
    try:
        slack_times, slack_sent = compute_slack_response_times()
        for rec in slack_times:
            rec["account"] = "slack"
        all_data.extend(slack_times)
        all_sent_counts["slack"] = slack_sent
        print(f"  → {len(slack_times)} reply events, {sum(slack_sent.values())} total sent")
    except Exception as e:
        print(f"  WARN: Slack failed: {e}")

    # Teams response times (from ibx tracking DB + Graph-derived for recent days)
    print("Fetching Teams...")
    try:
        teams_times = compute_teams_response_times()
        for rec in teams_times:
            rec["account"] = "teams"
        # Graph-derived replies catch messages sent from the Teams app directly
        # (the local DB only sees actions taken through ibx). We trust Graph
        # data for the recent window it covers and drop overlapping DB rows
        # for those days to avoid double-counting.
        graph_replies = compute_teams_replies_via_graph()
        graph_days = {r["date"] for r in graph_replies}
        if graph_days:
            teams_times = [r for r in teams_times if r["date"] not in graph_days]
        for rec in graph_replies:
            rec["account"] = "teams"
        teams_times.extend(graph_replies)
        all_data.extend(teams_times)
        print(f"  → {len(teams_times)} reply events "
              f"({len(graph_replies)} from Graph, "
              f"covering {len(graph_days)} day(s))")
        teams_sent_counts = compute_teams_sent_counts()
        if teams_sent_counts:
            all_sent_counts["teams"] = teams_sent_counts
            print(f"  → {sum(teams_sent_counts.values())} total sent")
    except Exception as e:
        print(f"  WARN: Teams failed: {e}")

    # Outlook response times (from ibx tracking DB)
    print("Fetching Outlook...")
    try:
        outlook_times = compute_outlook_response_times()
        for rec in outlook_times:
            rec["account"] = "outlook"
        all_data.extend(outlook_times)
        print(f"  → {len(outlook_times)} reply events")
        outlook_sent_counts = compute_outlook_sent_counts()
        if outlook_sent_counts:
            all_sent_counts["outlook"] = outlook_sent_counts
            print(f"  → {sum(outlook_sent_counts.values())} total sent")
    except Exception as e:
        print(f"  WARN: Outlook failed: {e}")

    daily, summary = build_stats(all_data, sent_counts=all_sent_counts)
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
