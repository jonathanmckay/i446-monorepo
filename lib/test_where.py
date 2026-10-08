"""where.py: device priority with observed-move override, never-backwards hold."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import where

H = 3600


def rep(tz, since, seen):
    return {"tz": tz, "since": since, "seen": seen}


def test_priority_order():
    r = {"ix": rep("America/Los_Angeles", 0, 100 * H),
         "straylight": rep("Asia/Tokyo", 10 * H, 100 * H),
         "imago": rep("Asia/Tokyo", 10 * H, 50 * H)}
    assert where.pick(r) == ("imago", "Asia/Tokyo")


def test_phone_beats_laptop_left_home():
    # laptop stayed home (Pacific, fresh); phone travelled and reported Tokyo
    r = {"straylight": rep("America/Los_Angeles", 0, 100 * H),
         "imago": rep("Asia/Tokyo", 90 * H, 95 * H)}
    assert where.pick(r)[1] == "Asia/Tokyo"


def test_laptop_move_after_phone_last_seen_wins():
    # flew home; laptop flipped to Pacific after the phone's last Tokyo report
    r = {"imago": rep("Asia/Tokyo", 10 * H, 50 * H),
         "straylight": rep("America/Los_Angeles", 60 * H, 100 * H)}
    assert where.pick(r) == ("straylight", "America/Los_Angeles")


def test_stale_phone_does_not_expire_to_ix():
    # Ix is always fresh but never moves: it must not win by freshness alone
    r = {"imago": rep("Asia/Tokyo", 10 * H, 11 * H),
         "ix": rep("America/Los_Angeles", 0, 1000 * H)}
    assert where.pick(r)[1] == "Asia/Tokyo"


def test_no_reports():
    assert where.pick({}) is None


def test_hold_blocks_backwards_date():
    now = dt.datetime(2026, 10, 9, 1, 0, tzinfo=dt.timezone.utc)  # Tokyo 10:00 10/9, LA 18:00 10/8
    last = {"tz": "Asia/Tokyo", "date": "2026-10-09"}
    assert where.apply_hold("America/Los_Angeles", last, now) == "Asia/Tokyo"
    later = dt.datetime(2026, 10, 9, 8, 0, tzinfo=dt.timezone.utc)  # LA 01:00 10/9: caught up
    assert where.apply_hold("America/Los_Angeles", last, later) == "America/Los_Angeles"


def test_hold_allows_forward():
    now = dt.datetime(2026, 10, 8, 20, 0, tzinfo=dt.timezone.utc)  # LA 13:00 10/8, Tokyo 05:00 10/9
    last = {"tz": "America/Los_Angeles", "date": "2026-10-08"}
    assert where.apply_hold("Asia/Tokyo", last, now) == "Asia/Tokyo"


def test_record_throttles_and_tracks_since(tmp_path, monkeypatch):
    monkeypatch.setattr(where, "WHERE_DIR", tmp_path)
    assert where.record("imago", "Asia/Tokyo", now=1000)
    assert not where.record("imago", "Asia/Tokyo", now=1100)          # throttled
    assert where.record("imago", "Asia/Tokyo", now=1000 + where.SEEN_EVERY)
    r = where._read("imago")
    assert r["since"] == 1000 and r["seen"] == 1000 + where.SEEN_EVERY
    assert where.record("imago", "America/Los_Angeles", now=9999)      # change: new since
    assert where._read("imago")["since"] == 9999
    assert not where.record("imago", "Not/AZone", now=10**6)
    assert not where.record("evilbox", "Asia/Tokyo", now=10**6)


def test_peer_device_rejects_non_tailscale():
    assert where.peer_device("127.0.0.1") is None
    assert where.peer_device("192.168.1.5") is None


def test_record_peer_accepts_phones_only(tmp_path, monkeypatch):
    monkeypatch.setattr(where, "WHERE_DIR", tmp_path)
    monkeypatch.setattr(where, "peer_device", lambda ip: {"1": "imago", "2": "straylight"}.get(ip))
    assert where.record_peer("1", "Asia/Tokyo") == "imago"
    assert where.record_peer("2", "Asia/Tokyo") is None, "Macs self-report; one writer per file"
    assert not (tmp_path / "straylight.json").exists()
