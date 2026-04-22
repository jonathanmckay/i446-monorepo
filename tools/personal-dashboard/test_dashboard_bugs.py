"""Regression tests for personal dashboard bugs."""
import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_gen_email_stats_in_cron():
    """Bug: gen_email_stats.py was not in cron, so the email response time
    gist went stale and the Project Bocking chart showed no data for recent days.
    Fix: added gen_email_stats.py to cron (every 6 hours).
    """
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    cron = result.stdout
    assert "gen_email_stats" in cron, (
        "gen_email_stats.py must be in crontab to keep the email stats gist fresh"
    )


def test_imsg_response_db_in_cron():
    """The iMessage response DB scanner must run on cron to provide live data."""
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    cron = result.stdout
    assert "imsg_response_db" in cron, (
        "imsg_response_db.py must be in crontab"
    )


def _load_gen_email_stats():
    module_path = Path(__file__).with_name("gen_email_stats.py")
    spec = importlib.util.spec_from_file_location("gen_email_stats", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_compute_teams_response_times_counts_only_replies_in_local_day(tmp_path, monkeypatch):
    """Teams bars should reflect sent replies, bucketed by local day."""
    db_path = tmp_path / ".config" / "teams" / "response_times.db"
    db_path.parent.mkdir(parents=True)

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE teams_responses (
            item_id TEXT PRIMARY KEY,
            sender TEXT,
            preview TEXT,
            fetched_at TEXT,
            action TEXT,
            action_at TEXT,
            response_hours REAL
        )
    """)
    conn.executemany(
        "INSERT INTO teams_responses VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "reply-prev-local-day",
                "Alice",
                "late night reply",
                "2026-04-12T03:30:00+00:00",
                "reply",
                "2026-04-12T04:06:16+00:00",
                0.6,
            ),
            (
                "reply-today",
                "Bob",
                "same local day",
                "2026-04-12T14:00:00+00:00",
                "reply",
                "2026-04-12T15:00:00+00:00",
                1.0,
            ),
            (
                "archive-today",
                "Carol",
                "handled without reply",
                "2026-04-12T16:00:00+00:00",
                "archive",
                "2026-04-12T17:00:00+00:00",
                1.0,
            ),
        ],
    )
    conn.commit()
    conn.close()

    gen_email_stats = _load_gen_email_stats()
    monkeypatch.setattr(gen_email_stats.Path, "home", lambda: tmp_path)

    rows = gen_email_stats.compute_teams_response_times(days=30)

    assert rows == [
        {"date": "2026-04-11", "hours": 0.6, "sent_hour": 21},
        {"date": "2026-04-12", "hours": 1.0, "sent_hour": 8},
    ]


def test_parse_teams_sent_counts_uses_actual_sent_messages_and_local_day():
    gen_email_stats = _load_gen_email_stats()

    raw = {
        "rawResponse": __import__("json").dumps({
            "value": [{
                "hitsContainers": [{
                    "hits": [
                        {
                            "resource": {
                                "createdDateTime": "2026-04-12T17:05:40Z",
                                "from": {"emailAddress": {
                                    "name": "Jonathan McKay",
                                    "address": "jomckay@microsoft.com",
                                }},
                            }
                        },
                        {
                            "resource": {
                                "createdDateTime": "2026-04-12T06:24:57Z",
                                "from": {"emailAddress": {
                                    "name": "Jonathan McKay",
                                    "address": "jomckay@microsoft.com",
                                }},
                            }
                        },
                        {
                            "resource": {
                                "createdDateTime": "2026-04-12T16:55:23Z",
                                "from": {"emailAddress": {
                                    "name": "Jacky Huang",
                                    "address": "jacky.huang@microsoft.com",
                                }},
                            }
                        },
                    ]
                }]
            }]
        })
    }

    counts = gen_email_stats.parse_teams_sent_counts(
        __import__("json").dumps(raw),
        days=30,
        now=gen_email_stats.datetime(2026, 4, 12, 18, 0, tzinfo=gen_email_stats.timezone.utc),
    )

    assert counts == {
        "2026-04-11": 1,
        "2026-04-12": 1,
    }


def test_slack_scans_mpim_channels():
    """
    Bug: Slack DM scan used types="im" which only finds 1:1 DMs. Group DMs
    (MPIMs) were missed, undercounting Slack replies and sent messages.

    Fix: Use types="im,mpim" to include both 1:1 and group DMs.
    """
    source = Path(__file__).parent.joinpath("gen_email_stats.py").read_text()
    assert '"im,mpim"' in source or "'im,mpim'" in source, (
        "compute_slack_response_times must use types='im,mpim' to include group DMs"
    )


def test_outlook_sent_counts_exist():
    """
    Bug: Outlook had no sent count computation. Proactive bars were always 0
    because only reply response times were tracked, not total sent messages.

    Fix: Add compute_outlook_sent_counts() that queries Graph API for sent
    mail, and wire it into main() so all_sent_counts["outlook"] is populated.
    """
    source = Path(__file__).parent.joinpath("gen_email_stats.py").read_text()
    assert "def compute_outlook_sent_counts" in source, (
        "gen_email_stats.py must have a compute_outlook_sent_counts function"
    )
    assert 'all_sent_counts["outlook"]' in source, (
        "main() must populate all_sent_counts['outlook'] from compute_outlook_sent_counts"
    )


def test_imessage_blended_avg_uses_response_count_not_sent_count():
    """
    Bug: dashboard.py used sent_count as the weight for iMessage in the
    blended response time average. sent_count includes proactive messages
    (no inbound to respond to), inflating iMessage's weight and dragging
    the blended average toward iMessage's (often high) avg_response_hours.

    Fix: SELECT response_count from daily_stats and use it as "count".
    """
    source = Path(__file__).parent.joinpath("dashboard.py").read_text()
    # The iMessage query must select response_count
    assert "response_count" in source, (
        "dashboard.py iMessage query must SELECT response_count from daily_stats"
    )
    # The count field must use response_count, not sent_count
    import re
    # Find the iMessage block and verify count uses resp_count
    imsg_block = source[source.index("# Add iMessage"):source.index("# Two blended")]
    assert '"count": resp_count' in imsg_block or "'count': resp_count" in imsg_block, (
        "iMessage 'count' must be set to resp_count (response_count), not sent"
    )


def test_imsg_response_db_skips_followup_messages():
    """Bug: imsg_response_db.py counted follow-up sent messages as separate
    'responses'. If the user sent 3 messages in a row after receiving one,
    all 3 were paired with the same received message — inflating the
    response count and distorting the average response time.

    The dashboard showed 97m avg while ibx0 showed 29m because iMessage's
    inflated count and overnight outliers dominated the blended mean.

    Fix: scan_chatdb only creates a response pair for the FIRST sent
    message after a received message. Consecutive sent messages (follow-ups)
    are skipped.
    """
    import ast
    source = Path(__file__).parent.joinpath("imsg_response_db.py").read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "scan_chatdb":
            body_src = ast.get_source_segment(source, node)

            # Must check if previous message is from_me to skip follow-ups
            assert "is_from_me" in body_src, (
                "scan_chatdb must check is_from_me on the previous message"
            )
            assert "i - 1" in body_src or "i-1" in body_src, (
                "scan_chatdb must look at msgs_list[i-1] to detect follow-ups"
            )
            assert "continue" in body_src, (
                "scan_chatdb must skip (continue) follow-up sent messages"
            )
            break
    else:
        raise AssertionError("scan_chatdb() not found in imsg_response_db.py")


def test_build_stats_emits_today_row_when_graph_search_fails():
    """Bug: when `compute_teams_sent_counts` timed out (Graph search returns
    None), it returned `{}`. With no DB replies for today either, `build_stats`
    never emitted a `(today, "teams")` row, so the dashboard chart silently
    dropped today's Teams bar entirely. The user sees nothing, indistinguishable
    from "no outage, no activity" — completely wrong signal.

    Fix: `build_stats` always emits a row for today for every account that has
    any activity in the last 7 days, even if all values are 0. Failures become
    a visible "0" rather than an invisible drop.
    """
    import sys
    from datetime import date, timedelta
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    import gen_email_stats

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    two_days_ago = (date.today() - timedelta(days=2)).isoformat()

    # Simulate: teams had activity yesterday + 2d ago but Graph timed out for
    # today (sent_counts is empty for teams). build_stats must still emit a
    # today row for teams.
    all_data = [
        {"date": yesterday, "account": "teams", "hours": 0.5, "recv_hour": 10},
        {"date": two_days_ago, "account": "teams", "hours": 0.3, "recv_hour": 14},
    ]
    daily, _summary = gen_email_stats.build_stats(all_data, sent_counts={})

    teams_today = [d for d in daily if d["date"] == today and d["account"] == "teams"]
    assert teams_today, (
        f"Expected a teams row for today ({today}) even when Graph search "
        f"returned no sent counts. Got dates: "
        f"{sorted({(d['date'], d['account']) for d in daily})}"
    )
    row = teams_today[0]
    assert row["count"] == 0 and row["sent_count"] == 0, (
        f"Today's empty teams row should be all zeros, got: {row}"
    )


def test_compute_teams_sent_counts_retries_with_smaller_window_on_timeout():
    """Bug: `compute_teams_sent_counts` made a single 2-day Graph search. When
    it timed out, today's count vanished — even though a 1-day query would
    likely have succeeded.

    Fix: progressively shrink the window (2d → 1d) so today's count survives a
    partial outage.
    """
    import ast
    src = open(__import__("pathlib").Path(__file__).parent / "gen_email_stats.py").read()
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "compute_teams_sent_counts"), None)
    assert fn is not None
    body_src = ast.get_source_segment(src, fn)
    # Must contain a loop over progressively smaller windows
    assert "for recent_days in" in body_src, (
        "compute_teams_sent_counts must iterate over progressively smaller "
        "windows so today's count survives Graph timeouts"
    )
    # Must distinguish "call failed" (raw is None) from "call succeeded with 0 hits"
    assert "raw is not None" in body_src or "raw is None" in body_src, (
        "compute_teams_sent_counts must distinguish call-failed from "
        "zero-hits to know when to retry with a smaller window"
    )


def test_parse_teams_replies_detects_app_sent_replies_via_graph():
    """Bug: Teams replies sent via the Teams app (not ibx) never reached the
    local response_times.db, so the inbound reply count for Teams was
    structurally undercounted on the dashboard.

    Fix: parse_teams_replies derives replies from a Graph search by grouping
    messages per chatId, sorting chronologically, and treating each (their →
    mine) transition as one reply event.
    """
    import sys, json
    from datetime import datetime, timezone, timedelta
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    sys.path.insert(0, "/Users/mckay/i446-monorepo/tools/ibx")
    import gen_email_stats

    now = datetime(2026, 4, 19, 22, 0, tzinfo=timezone.utc)
    inbound_ts = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    reply_ts   = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Two messages in the same chat: counterparty inbound, then me replying.
    raw = json.dumps({
        "rawResponse": json.dumps({
            "value": [{
                "hitsContainers": [{"hits": [
                    {"resource": {
                        "id": "m1", "chatId": "chat-A",
                        "createdDateTime": inbound_ts,
                        "from": {"emailAddress": {"address": "alice@example.com"}},
                    }},
                    {"resource": {
                        "id": "m2", "chatId": "chat-A",
                        "createdDateTime": reply_ts,
                        "from": {"emailAddress": {"address": "jomckay@microsoft.com"}},
                    }},
                    # Solo-mine in another chat — must NOT count as a reply.
                    {"resource": {
                        "id": "m3", "chatId": "chat-B",
                        "createdDateTime": reply_ts,
                        "from": {"emailAddress": {"address": "jomckay@microsoft.com"}},
                    }},
                    # Channel post — must be skipped.
                    {"resource": {
                        "id": "m4", "chatId": "chat-C",
                        "channelIdentity": {"channelId": "x@thread.tacv2"},
                        "createdDateTime": reply_ts,
                        "from": {"emailAddress": {"address": "jomckay@microsoft.com"}},
                    }},
                ]}],
            }],
        }),
    })

    replies = gen_email_stats.parse_teams_replies(raw, days=2, now=now)
    assert len(replies) == 1, (
        f"Expected exactly 1 reply event (alice→me in chat-A), got {len(replies)}: {replies}"
    )
    r = replies[0]
    assert abs(r["hours"] - 1.0) < 0.01, f"reply latency must be 1h, got {r['hours']}"
    assert "date" in r and "recv_hour" in r
