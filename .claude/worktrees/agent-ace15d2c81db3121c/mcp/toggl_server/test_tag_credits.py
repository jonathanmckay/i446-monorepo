"""JM 2026-09-16: "for /did I want to make sure if there is a tag on the
task those points get recorded (i.e. through airport should have 29 that
goes to -2 hcmc)". Value-tag minutes are credited on stop, once, from the
one choke point every stop path uses (toggl_api.stop_timer)."""
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load():
    spec = importlib.util.spec_from_file_location("tag_credits_t", HERE / "tag_credits.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tc(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.setattr(mod, "STATE_PATH", tmp_path / "janus-tag-credits.json")
    monkeypatch.setattr(mod, "_shortcodes", {"新闻": {"-3"}, "hiit": {"-2"}, "work": set()})
    monkeypatch.setattr(mod, "_tag_col", lambda tag: {"-1": "AV", "-2": "AW", "-3": "AX"}[tag])
    writes = []
    monkeypatch.setattr(mod, "_append", lambda *a, **k: writes.append((a, k)))
    mod._writes = writes
    return mod


NOW = dt.datetime(2026, 9, 8, 11, 31, tzinfo=TZ)


def _entry(desc, tags, mins=29, eid=4546227744):
    start = NOW - dt.timedelta(minutes=mins)
    return {"id": eid, "description": desc, "tags": tags,
            "start": start.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "stop": NOW.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "duration": mins * 60}


def test_explicit_value_tag_credits_minutes_to_the_tag_column(tc):
    out = tc.credit_entry(_entry("through airport", ["-2"]), now=NOW)
    assert out == ["#-2 +29m → 0n"]
    (sheet, col), kw = tc._writes[0]
    assert (sheet, col, kw["date"], kw["value"]) == ("0n", "AW", "9/8", "+29")
    assert "through airport" in kw["src"]
    st = json.loads(tc.STATE_PATH.read_text())
    assert st["credited"] == ["4546227744:-2"]


def test_shortcode_auto_tag_is_not_credited(tc):
    """新闻 auto-tags -3 and already earns its own 0n points — crediting the
    tag again would double count (janus applies the same rule)."""
    assert tc.credit_entry(_entry("新闻", ["-3"]), now=NOW) == []
    assert tc._writes == []


def test_explicit_tag_on_a_shortcode_entry_still_credits_when_not_implied(tc):
    # "work" has no auto tags; a hand-typed #-1 on it is a deliberate claim.
    assert tc.credit_entry(_entry("work", ["-1"]), now=NOW) == ["#-1 +29m → 0n"]


def test_already_credited_key_is_skipped_and_pending_is_resolved(tc):
    tc.STATE_PATH.write_text(json.dumps({
        "date": NOW.date().isoformat(), "credited": ["4546227744:-2"],
        "pending": [{"key": "4546227744:-1", "id": 4546227744, "tag": "-1"}]}))
    out = tc.credit_entry(_entry("through airport", ["-2", "-1"]), now=NOW)
    assert out == ["#-1 +29m → 0n"]           # -2 already paid by janus; -1 paid here
    st = json.loads(tc.STATE_PATH.read_text())
    assert st["pending"] == []                 # janus won't pay -1 a second time
    assert sorted(st["credited"]) == ["4546227744:-1", "4546227744:-2"]


def test_non_value_tags_and_zero_minutes_are_ignored(tc):
    assert tc.credit_entry(_entry("call", ["d359/carol-bryan", "focus"]), now=NOW) == []
    assert tc.credit_entry(_entry("blip", ["-2"], mins=0), now=NOW) == []


def test_write_failure_does_not_journal_the_credit(tc, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ix unreachable")
    monkeypatch.setattr(tc, "_append", boom)
    assert tc.credit_entry(_entry("through airport", ["-2"]), now=NOW) == []
    assert not tc.STATE_PATH.exists()


def test_unloadable_shortcode_table_means_no_credit(tc, monkeypatch):
    monkeypatch.setattr(tc, "_auto_tags_for", lambda d: None)
    assert tc.credit_entry(_entry("through airport", ["-2"]), now=NOW) == []


def test_stop_timer_attaches_credits_to_the_entry(monkeypatch):
    sys.path.insert(0, str(HERE.parent))
    from toggl_server import tag_credits as real_tc
    from toggl_server import toggl_api as api
    monkeypatch.setattr(api, "_request", lambda *a, **k: {"id": 1, "tags": ["-2"], "description": "x"})
    monkeypatch.setattr(real_tc, "credit_entry", lambda e: ["#-2 +5m → 0n"])
    entry = api.stop_timer(1)
    assert entry["_tag_credits"] == ["#-2 +5m → 0n"]


def test_stop_timer_survives_a_credit_failure(monkeypatch):
    sys.path.insert(0, str(HERE.parent))
    from toggl_server import tag_credits as real_tc
    from toggl_server import toggl_api as api
    monkeypatch.setattr(api, "_request", lambda *a, **k: {"id": 1, "tags": ["-2"], "description": "x"})
    def boom(e):
        raise RuntimeError("no")
    monkeypatch.setattr(real_tc, "credit_entry", boom)
    entry = api.stop_timer(1)
    assert entry["_tag_credits"] == [] and entry["id"] == 1


def test_update_entry_stop_on_the_running_entry_credits(monkeypatch):
    """janus retime / janus-mobile close the running entry with
    update_entry(stop=...), never stop_timer — same credit (2026-09-17)."""
    sys.path.insert(0, str(HERE.parent))
    from toggl_server import tag_credits as real_tc
    from toggl_server import toggl_api as api
    monkeypatch.setattr(api, "get_current_cached", lambda *a, **k: {"id": 7})
    monkeypatch.setattr(api, "_request", lambda *a, **k: {"id": 7, "tags": ["-2"], "description": "x"})
    monkeypatch.setattr(real_tc, "credit_entry", lambda e: ["#-2 +5m → 0n"])
    entry = api.update_entry(7, stop="2026-09-17T15:00:00Z")
    assert entry["_tag_credits"] == ["#-2 +5m → 0n"]


def test_update_entry_retime_of_a_closed_entry_does_not_credit(monkeypatch):
    sys.path.insert(0, str(HERE.parent))
    from toggl_server import tag_credits as real_tc
    from toggl_server import toggl_api as api
    monkeypatch.setattr(api, "get_current_cached", lambda *a, **k: {"id": 99})
    monkeypatch.setattr(api, "_request", lambda *a, **k: {"id": 7, "tags": ["-2"], "description": "x"})
    called = []
    monkeypatch.setattr(real_tc, "credit_entry", lambda e: called.append(e) or [])
    entry = api.update_entry(7, stop="2026-09-17T15:00:00Z")
    assert called == [] and "_tag_credits" not in entry
