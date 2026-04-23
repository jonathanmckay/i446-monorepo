"""Comms response-time clamp.

Resets the response timer at local midnight (America/Los_Angeles) for each
individual message and caps the result at 24h. Used by both the iMessage /
Outlook / Gmail / Teams sync scripts and the dashboard renderer so that
"response time" reflects today's effective wait, not the cumulative debt of
an unanswered message that has been sitting in the queue for days.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PST = ZoneInfo("America/Los_Angeles")
MAX_RESPONSE_HOURS = 24.0  # 1440 min


def clamp_response_hours_unix(sent_ts: float, recv_ts: float) -> float:
    """Compute response hours from unix-second timestamps with the daily reset.

    The effective received time is `max(actual_recv, last_local_midnight_at_or_before(sent))`,
    so any message older than the most recent PST midnight is treated as if
    it had just been received at midnight. Result is clamped to [0, 24].
    """
    sent_local = datetime.fromtimestamp(sent_ts, tz=PST)
    midnight_ts = sent_local.replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    effective_recv = max(recv_ts, midnight_ts)
    return max(0.0, min((sent_ts - effective_recv) / 3600.0, MAX_RESPONSE_HOURS))


def clamp_response_hours_dt(sent_dt: datetime, recv_dt: datetime) -> float:
    """Same as `clamp_response_hours_unix` but accepts datetimes.

    Naive datetimes are assumed UTC.
    """
    if sent_dt.tzinfo is None:
        sent_dt = sent_dt.replace(tzinfo=timezone.utc)
    if recv_dt.tzinfo is None:
        recv_dt = recv_dt.replace(tzinfo=timezone.utc)
    return clamp_response_hours_unix(sent_dt.timestamp(), recv_dt.timestamp())
