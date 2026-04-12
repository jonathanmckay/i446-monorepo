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
        {"date": "2026-04-11", "hours": 0.6},
        {"date": "2026-04-12", "hours": 1.0},
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
