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


# ── unread-inbox snapshot (2026-11-02) ──────────────────────────────────────

def _graph_msg(i, read=False):
    return {"id": f"m{i}", "subject": f"s{i}", "receivedDateTime": "2026-11-02T01:00:00Z",
            "from": {"emailAddress": {"name": "Ben Lee", "address": "Ben@Microsoft.com"}},
            "bodyPreview": "word " * 200, "importance": "high", "isRead": read,
            "conversationId": f"c{i}"}


class _MailMcp:
    payload = None
    calls = []

    @classmethod
    def call_tool(cls, server, tool, args, timeout=None):
        cls.calls.append((server, tool, args))
        if isinstance(cls.payload, Exception):
            raise cls.payload
        return cls.payload


def _inbox_env(tmp_path, monkeypatch, payload):
    mod = outlook_client
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(mod, "cache_only", lambda: False)
    _MailMcp.payload, _MailMcp.calls = payload, []
    monkeypatch.setattr(mod, "mcp", _MailMcp)
    return mod


NOW = dt.datetime(2026, 11, 2, 11, 0, tzinfo=dt.timezone.utc)


def test_fetch_unread_inbox_writes_snapshot_double_encoded(tmp_path, monkeypatch):
    raw = {"content": [{"type": "text", "text": json.dumps(
        {"rawResponse": json.dumps({"value": [_graph_msg(1), _graph_msg(2)]})})}]}
    mod = _inbox_env(tmp_path, monkeypatch, raw)
    snap = mod.fetch_unread_inbox(now=NOW)
    day = NOW.astimezone(mod.TZ).date()
    on_disk = json.loads((tmp_path / f"outlook-inbox-{day}.json").read_text())
    assert on_disk == snap and snap["count"] == 2 and snap["window_hours"] == 48
    m = snap["messages"][0]
    assert set(m) >= {"sender", "subject", "received", "preview", "importance",
                      "isRead", "conversationId"}
    assert m["sender_email"] == "ben@microsoft.com"
    assert len(m["preview"]) <= 300
    q = _MailMcp.calls[0][2]["queryParameters"]
    assert "isRead eq false" in q and "2026-10-31T11:00:00Z" in q  # 48h window


def test_fetch_unread_inbox_plain_payload_with_status_line(tmp_path, monkeypatch):
    raw = {"content": [{"text": "Messages retrieved.\n" + json.dumps({"value": [_graph_msg(1)]})}]}
    mod = _inbox_env(tmp_path, monkeypatch, raw)
    assert mod.fetch_unread_inbox(now=NOW)["count"] == 1


def test_fetch_unread_inbox_failure_keeps_prior_snapshot(tmp_path, monkeypatch):
    mod = _inbox_env(tmp_path, monkeypatch, {"content": [{"text": "error: timeout"}]})
    day = NOW.astimezone(mod.TZ).date()
    good = tmp_path / f"outlook-inbox-{day}.json"
    good.write_text('{"count": 3}')
    import pytest
    with pytest.raises(RuntimeError):
        mod.fetch_unread_inbox(now=NOW)
    assert json.loads(good.read_text()) == {"count": 3}
    _MailMcp.payload = TimeoutError("agency")
    with pytest.raises(TimeoutError):
        mod.fetch_unread_inbox(now=NOW)
    assert json.loads(good.read_text()) == {"count": 3}


def test_fetch_unread_inbox_refuses_on_cache_only_host(tmp_path, monkeypatch):
    mod = _inbox_env(tmp_path, monkeypatch, None)
    monkeypatch.setattr(mod, "cache_only", lambda: True)
    import pytest
    with pytest.raises(RuntimeError):
        mod.fetch_unread_inbox(now=NOW)
    assert _MailMcp.calls == []


def test_inbox_snapshot_rides_the_existing_rsync_glob():
    """The daemon pushes CACHE_DIR.glob('outlook-*.json'); the snapshot name
    must match it, and the temp file must not."""
    import fnmatch
    p = outlook_client.inbox_cache_path(dt.date(2026, 11, 2))
    assert fnmatch.fnmatch(p.name, "outlook-*.json")
    assert not fnmatch.fnmatch(p.with_suffix(".json.tmp").name, "outlook-*.json")
