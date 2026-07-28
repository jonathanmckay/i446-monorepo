import datetime as dt

import outlook_client


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
