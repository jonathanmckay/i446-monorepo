"""Regression tests for personal dashboard bugs."""
import subprocess


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
