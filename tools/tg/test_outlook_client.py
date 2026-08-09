import datetime as dt
import inspect
import json
import os

import outlook_client


def test_fetch_uses_calendar_view_not_list_events():
    """Regression (user report 2026-07-27: "janus is missing a lot of the
    meetings on my calendar today"): Graph's ListEvents returns recurring
    series MASTERS (original start date, outside the day window), so every
    recurring meeting — standups, weekly 1:1s, holds — vanished. Only
    ListCalendarView expands series into the day's actual instances."""
    src = inspect.getsource(outlook_client.list_events)
    assert '"ListCalendarView"' in src
    assert '"ListEvents"' not in src
    # Datetimes must carry a UTC offset per the tool's contract — a naive
    # local string is ambiguous server-side.
    assert "isoformat()" in src


def test_normalize_preserves_graph_windows_timezone():
    raw = [{
        "subject": "Morning meeting",
        "start": "2026-06-04T09:00:00.0000000",
        "end": "2026-06-04T09:30:00.0000000",
        "start_tz": "Pacific Standard Time",
        "end_tz": "Pacific Standard Time",
        "is_all_day": False,
    }]

    events = outlook_client._normalize(raw)

    assert len(events) == 1
    assert events[0]["start_dt"] == dt.datetime(
        2026, 6, 4, 9, 0, tzinfo=outlook_client.TZ
    )
    assert events[0]["end_dt"] == dt.datetime(
        2026, 6, 4, 9, 30, tzinfo=outlook_client.TZ
    )


def test_normalize_legacy_cache_without_timezone_stays_utc():
    raw = [{
        "subject": "Legacy cached meeting",
        "start": "2026-06-04T09:00:00.0000000",
        "end": "2026-06-04T09:30:00.0000000",
        "is_all_day": False,
    }]

    events = outlook_client._normalize(raw)

    assert events[0]["start_dt"] == dt.datetime(
        2026, 6, 4, 2, 0, tzinfo=outlook_client.TZ
    )


def test_empty_fetch_does_not_clobber_nonempty_cache(tmp_path, monkeypatch):
    """2026-07-30: an Agency flake returning a successful-but-empty calendar
    view must not overwrite a good same-day cache — that blanked every
    meeting in janus for the cache TTL."""
    mod = outlook_client
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
    day = dt.datetime.now(mod.TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    cache = tmp_path / f"outlook-{day:%Y-%m-%d}.json"
    good = [{"subject": "Standup", "start": f"{day:%Y-%m-%d}T09:00:00",
             "end": f"{day:%Y-%m-%d}T09:30:00", "start_tz": "Pacific Standard Time",
             "end_tz": "Pacific Standard Time", "is_all_day": False}]
    cache.write_text(json.dumps(good))
    # cache is stale (mtime in the past) so the fetch path runs
    old = dt.datetime.now().timestamp() - 3600
    os.utime(cache, (old, old))

    class _EmptyMcp:
        @staticmethod
        def call_tool(*a, **k):
            return {"content": [{"text": "[]"}]}

    monkeypatch.setattr(mod, "mcp", _EmptyMcp)
    out = mod.list_events(day, day + dt.timedelta(days=1))
    assert [e["title"] for e in out] == ["Standup"], \
        "the prior non-empty cache must survive an empty fetch"
    assert json.loads(cache.read_text()), "cache file must not be clobbered"
