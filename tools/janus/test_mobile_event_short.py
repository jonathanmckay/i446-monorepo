"""Event titles get the shared Haiku short name server-side (2026-11-12, JM:
"make it so that haiku will shorten the event titles ... serverside so it's
common across CLI and iOS"). lib/event_shorts.py is the implementation; here we
pin how mobile.py uses it: the row's display title is the short, raw_title stays
raw, and a client that converts/logs the short gets mapped back to the raw title."""
import datetime as dt
import json

import sys
from pathlib import Path

from test_mobile import jm  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import event_shorts as es  # noqa: E402

RAW = "XCORE Weekly Business Review | Jonathan McKay, Jill Braff [in-person]"
SHORT = "XCORE WBR · Jill Braff"


def _event(jm, raw, h=9, m=0, mins=60):
    day0 = dt.datetime.now(jm._tz()).replace(hour=0, minute=0, second=0, microsecond=0)
    start = day0 + dt.timedelta(hours=h, minutes=m)
    return {"title": jm._safe_title(raw), "raw_title": raw, "start_dt": start,
            "end_dt": start + dt.timedelta(minutes=mins), "code": "i9", "color": "#123456",
            "is_past": True, "started": True, "event_id": "e1", "calendar_id": "c1"}


def _seed(tmp_path, monkeypatch, raw=RAW, short=SHORT):
    p = tmp_path / "event-shortnames.json"
    p.write_text(json.dumps({es.key(raw, es.DEFAULT_COLS): {"raw": raw, "short": short}}))
    monkeypatch.setattr(es, "PATH", p)
    return p


def test_event_row_title_is_cached_short_and_raw_title_stays_raw(jm, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(es, "haiku_shorten", lambda raw, cols: calls.append(raw) or "x")
    monkeypatch.setattr(jm, "_fetch_today", lambda *a: [])
    monkeypatch.setattr(jm, "_fetch_events_today", lambda *a, **k: [_event(jm, RAW)])
    d = jm.app.test_client().get("/api/timeline").get_json()
    ev = next(r for r in d["rows"] if r["type"] == "event")
    assert ev["title"] == SHORT
    assert ev["raw_title"] == RAW
    assert calls == [], "cache hit: no Haiku call on the request path"


def test_uncached_wide_title_ships_raw_now_and_fills_in_background(jm, monkeypatch, tmp_path):
    p = tmp_path / "event-shortnames.json"
    monkeypatch.setattr(es, "PATH", p)
    import threading
    release, done = threading.Event(), threading.Event()
    def slow_haiku(raw, cols):        # a real round-trip takes ~1 s; hold it until the response is out
        release.wait(5)
        return SHORT
    monkeypatch.setattr(es, "haiku_shorten", slow_haiku)
    monkeypatch.setattr(jm, "_fetch_today", lambda *a: [])
    monkeypatch.setattr(jm, "_fetch_events_today", lambda *a, **k: [_event(jm, RAW)])
    d = jm.app.test_client().get("/api/timeline").get_json()
    ev = next(r for r in d["rows"] if r["type"] == "event")
    assert ev["title"] == jm._safe_title(RAW), "first pass shows the raw title, never blocks on Haiku"
    release.set()
    import time
    for _ in range(100):              # the sidecar write follows the Haiku call
        if p.exists():
            break
        time.sleep(0.02)
    assert json.loads(p.read_text())[es.key(RAW, es.DEFAULT_COLS)] == {"raw": RAW, "short": SHORT}
    # next request: cached → the short, no Haiku
    monkeypatch.setattr(es, "haiku_shorten", lambda raw, cols: 1 / 0)
    d = jm.app.test_client().get("/api/timeline").get_json()
    assert next(r for r in d["rows"] if r["type"] == "event")["title"] == SHORT


def test_short_title_never_triggers_haiku(jm, monkeypatch, tmp_path):
    monkeypatch.setattr(es, "PATH", tmp_path / "event-shortnames.json")
    calls = []
    monkeypatch.setattr(es, "haiku_shorten", lambda raw, cols: calls.append(raw) or "x")
    monkeypatch.setattr(jm, "_fetch_today", lambda *a: [])
    monkeypatch.setattr(jm, "_fetch_events_today", lambda *a, **k: [_event(jm, "standup")])
    d = jm.app.test_client().get("/api/timeline").get_json()
    ev = next(r for r in d["rows"] if r["type"] == "event")
    assert ev["title"] == "standup" and calls == []


def test_convert_event_maps_echoed_short_back_to_raw_title(jm, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(jm, "_run_did_fast", lambda text, flags=(): sent.append(text) or {"ok": True, "step": "x"})
    start = dt.datetime.now(jm._tz()).replace(hour=9, minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(minutes=50)
    # an old web tab / un-updated phone sends the short it displayed
    r = jm.convert_event(SHORT, start.isoformat(), end.isoformat(), "i9", True)
    assert r["ok"]
    assert sent and sent[0].startswith(jm._safe_title(RAW) + " 0900-0950"), sent


def test_convert_event_unknown_title_passes_through(jm, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(jm, "_run_did_fast", lambda text, flags=(): sent.append(text) or {"ok": True})
    start = dt.datetime.now(jm._tz()).replace(hour=9, minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(minutes=30)
    jm.convert_event("coffee chat", start.isoformat(), end.isoformat(), "", True)
    assert sent[0].startswith("coffee chat 0900-0930")


def test_web_convert_sends_raw_title():
    from pathlib import Path
    src = (Path(__file__).parent / "mobile.py").read_text()
    assert "title:r.raw_title || r.title" in src, "web convert must send the raw title, not the displayed short"
