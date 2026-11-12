"""Long calendar event titles get dtd's Haiku short names: cached shorts apply
synchronously in fetch_gcal's path, misses fill via _fill_event_shorts, and the
detail band shows the event's exact start time (slots are 15-min, so a 09:05
start would otherwise read as 09:00)."""
import datetime as dtm
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")

LONG_TITLE = "XBOX + Battle.net Workstream sync with partner teams (recurring)"
SHORT_NAME = "XBOX+Battle.net sync"


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_evshort", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_evshort"] = mod
    spec.loader.exec_module(mod)
    return mod


def _hash(title, mod=None):
    # lib/event_shorts keys on (prompt version, cols, title) — same across surfaces.
    import event_shorts
    return event_shorts.key(title, mod.EVENT_SHORT_COLS if mod else 33)


def _event(title, start, end):
    return {"title": title, "start_dt": start, "end_dt": end}


class _StubShorten:
    """Stands in for lib/event_shorts.haiku_shorten (2026-11-12: the TUI no
    longer calls dtd's task shortener for events)."""
    def __init__(self, result=SHORT_NAME):
        self.result = result
        self.calls = []

    def _haiku_shorten(self, prose, cols=33):
        self.calls.append(prose)
        return self.result


def _stub(mod, monkeypatch, result=SHORT_NAME):
    stub = _StubShorten(result)
    monkeypatch.setattr(mod.event_shorts, "haiku_shorten", stub._haiku_shorten)
    mod._event_shorts_failed.clear()
    return stub


def test_cached_short_applies_synchronously(tmp_path, monkeypatch):
    mod = _load_tui()
    mod.EVENT_SHORTS = tmp_path / "shorts.json"
    mod.EVENT_SHORTS.write_text(json.dumps({_hash(LONG_TITLE, mod): {"raw": LONG_TITLE, "short": SHORT_NAME}}))
    stub = _stub(mod, monkeypatch)
    now = dtm.datetime.now(TZ)
    evs = [_event(LONG_TITLE, now, now + dtm.timedelta(hours=1))]
    mod._shorten_events(evs)
    assert evs[0]["short"] == SHORT_NAME
    assert stub.calls == [], "cache hit must not call Haiku"


def test_short_title_untouched(tmp_path, monkeypatch):
    mod = _load_tui()
    mod.EVENT_SHORTS = tmp_path / "shorts.json"
    stub = _stub(mod, monkeypatch)
    now = dtm.datetime.now(TZ)
    evs = [_event("standup", now, now + dtm.timedelta(minutes=30))]
    mod._shorten_events(evs)
    assert "short" not in evs[0]
    assert stub.calls == []


def test_width_trigger_is_display_cols_not_len(tmp_path, monkeypatch):
    """A 19-char CJK title is 38 display columns — must trigger."""
    mod = _load_tui()
    mod.EVENT_SHORTS = tmp_path / "shorts.json"
    stub = _stub(mod, monkeypatch, "短名")
    now = dtm.datetime.now(TZ)
    cjk = "季度战略评审会议与产品路线图深度讨论会"  # 19 CJK chars = 38 cols
    ev = _event(cjk, now, now)
    misses = mod.event_shorts.apply_cached([ev], mod.EVENT_SHORT_COLS, mod.EVENT_SHORTS)
    assert list(misses) == [_hash(cjk, mod)], "38 cols > 33 must be a miss"
    mod._fill_event_shorts(misses)
    assert stub.calls == [cjk]


def test_fill_persists_and_applies_to_all_same_title(tmp_path, monkeypatch):
    mod = _load_tui()
    mod.EVENT_SHORTS = tmp_path / "shorts.json"
    stub = _stub(mod, monkeypatch)
    now = dtm.datetime.now(TZ)
    a = _event(LONG_TITLE, now, now)
    b = _event(LONG_TITLE, now + dtm.timedelta(hours=3), now)  # recurring twin
    mod._fill_event_shorts({_hash(LONG_TITLE, mod): [a, b]})
    assert a["short"] == SHORT_NAME and b["short"] == SHORT_NAME
    assert len(stub.calls) == 1, "one Haiku call per unique title"
    assert json.loads(mod.EVENT_SHORTS.read_text()) == {
        _hash(LONG_TITLE, mod): {"raw": LONG_TITLE, "short": SHORT_NAME}}


def test_fill_failure_negative_cached(tmp_path, monkeypatch):
    mod = _load_tui()
    mod.EVENT_SHORTS = tmp_path / "shorts.json"
    _stub(mod, monkeypatch, result=None)
    now = dtm.datetime.now(TZ)
    ev = _event(LONG_TITLE, now, now)
    mod._fill_event_shorts({_hash(LONG_TITLE, mod): [ev]})
    assert "short" not in ev
    assert LONG_TITLE in mod._event_shorts_failed
    # next _shorten_events pass must skip it entirely
    stub = _StubShorten()
    monkeypatch.setattr(mod.event_shorts, "haiku_shorten", stub._haiku_shorten)
    mod._shorten_events([ev])
    assert stub.calls == []


def test_slot_label_shows_exact_start_and_short(tmp_path):
    mod = _load_tui()
    now = dtm.datetime.now(TZ)
    start = now.replace(hour=9, minute=5, second=0, microsecond=0)
    ev = _event(LONG_TITLE, start, start + dtm.timedelta(hours=1))
    ev["short"] = SHORT_NAME
    mod.STATE.events = [ev]
    label, _ = mod._slot_label_gcal(start.replace(minute=0), start.replace(minute=15))
    assert label == f"◇ 09:05 {SHORT_NAME}"
    assert mod.dwidth(label) <= mod.WIDTH_HINT - 6 - 3, "label must fit a detail row"
