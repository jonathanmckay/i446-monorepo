"""Regression tests for the occupancy fetcher's event-log seeding."""
import datetime as dt
import fetch


def test_baseline_seed_uses_real_move_out_not_today(tmp_path, monkeypatch):
    """A unit that already moved out must be dated by its move-out, not today.

    Regression: the cold-start seed called add() with no `known=`, so every
    baseline event defaulted to today — 'tagging all the events as happening
    today, when that obviously can't be true'.
    """
    monkeypatch.setattr(fetch, "DATA", tmp_path)
    monkeypatch.setattr(fetch, "ARCH", tmp_path / "unit-archive")
    today = dt.date(2026, 6, 14)
    vac = [{"unit_id": 1, "unit_status": "Vacant-Unrented", "last_move_out": "2026-01-12",
            "next_move_in": None, "property_name": "rl16", "unit": "D10"}]
    events = fetch.update_events(vac, [], today)
    vac_ev = next(e for e in events if e["kind"] == "vacant")
    assert vac_ev["known"] == "2026-01-12", f"expected real move-out date, got {vac_ev['known']}"
    assert vac_ev["known"] != today.isoformat()


def test_baseline_leased_uses_lease_sign_date(tmp_path, monkeypatch):
    """A rented unit's baseline event is dated by its real lease_sign_date."""
    monkeypatch.setattr(fetch, "DATA", tmp_path)
    monkeypatch.setattr(fetch, "ARCH", tmp_path / "unit-archive")
    today = dt.date(2026, 6, 14)
    vac = [{"unit_id": 7, "unit_status": "Vacant-Rented", "last_move_out": "2026-05-01",
            "next_move_in": "2026-06-25", "property_name": "kn47", "unit": "F106"}]
    le = [{"unit_id": 7, "lease_sign_date": "2026-05-21", "move_in": "2026-06-25"}]
    events = fetch.update_events(vac, le, today)
    leased = next(e for e in events if e["kind"] == "leased")
    assert leased["known"] == "2026-05-21", f"expected lease_sign_date, got {leased['known']}"


def test_baseline_future_notice_caps_to_today(tmp_path, monkeypatch):
    """A current notice with a future move-out is 'known as of today', effective later."""
    monkeypatch.setattr(fetch, "DATA", tmp_path)
    monkeypatch.setattr(fetch, "ARCH", tmp_path / "unit-archive")
    today = dt.date(2026, 6, 14)
    vac = [{"unit_id": 2, "unit_status": "Notice-Unrented", "last_move_out": "2026-07-21",
            "next_move_in": None, "property_name": "a511", "unit": "102"}]
    events = fetch.update_events(vac, [], today)
    ntv = next(e for e in events if e["kind"] == "ntv")
    assert ntv["known"] == today.isoformat()      # can't have learned it in the future
    assert ntv["effective"] == "2026-07-21"        # but it happens later
