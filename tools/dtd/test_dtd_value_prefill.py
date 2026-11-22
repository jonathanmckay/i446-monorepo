#!/usr/bin/env python3
"""A running variable habit's value prompt opens with the timer's minutes
(2026-11-22: "if hiit is running and the hiit survey comes up, it's prefilled
with the current number")."""
import importlib.util
import json
import sys
import time
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("dtd_prefill", Path(__file__).parent / "dtd.py")
dtd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dtd)

ISO = "2026-11-22"


def _cache(tmp_path, entries):
    p = tmp_path / "day.json"
    p.write_text(json.dumps({ISO: entries}))
    return p


def _e(desc, start, end, running=False):
    return {"desc": desc, "start_dt": f"{ISO}T{start}-08:00", "end_dt": f"{ISO}T{end}-08:00", "running": running}


def test_hiit_prefill_is_the_days_total_base_plus_the_running_start(tmp_path, monkeypatch):
    """did-fast SETS hiit to the day's Toggl total, so an earlier session counts."""
    monkeypatch.setattr(dtd, "JANUS_DAY_CACHE", _cache(tmp_path, [
        _e("hiit", "07:00:00", "07:12:00"), _e("xk20", "08:00:00", "08:30:00"),
        _e("hiit", "09:04:19", "09:04:19", running=True)]))
    assert dtd.value_prefill("hiit (10) [23]", True, 1700000000, ISO) == {"runningSince": 1700000000, "prefillBaseSecs": 720}
    assert dtd.value_prefill("hiit 11.21", True, 1700000000, ISO)["prefillBaseSecs"] == 720      # a deferred copy


def test_kid_time_and_news_prefill_the_running_session_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(dtd, "JANUS_DAY_CACHE", _cache(tmp_path, [_e("xk20", "08:00:00", "08:30:00")]))
    for card in ("xk20 (20) [40]", "xk22", "xk26", "新闻 (15)"):
        assert dtd.value_prefill(card, True, 1700000000, ISO) == {"runningSince": 1700000000, "prefillBaseSecs": 0}


def test_no_prefill_unless_it_is_the_running_card_of_a_listed_habit(tmp_path, monkeypatch):
    monkeypatch.setattr(dtd, "JANUS_DAY_CACHE", _cache(tmp_path, []))
    assert dtd.value_prefill("hiit", False, 1700000000, ISO) == {}          # not running
    assert dtd.value_prefill("hiit", True, 0, ISO) == {}                    # start unknown
    for card in ("冥想", "o314", "hcmr", "cpap", "i444", "长冥想", "ship inkwell"):
        assert dtd.value_prefill(card, True, 1700000000, ISO) == {}         # day-total family, scores, plain tasks


def test_stale_or_missing_day_cache_means_no_base(tmp_path):
    assert dtd._stopped_secs_today("hiit", ISO, path=tmp_path / "nope.json") == 0
    p = _cache(tmp_path, [_e("hiit", "07:00:00", "07:12:00")])
    assert dtd._stopped_secs_today("hiit", ISO, path=p) == 720
    assert dtd._stopped_secs_today("hiit", ISO, now=time.time() + dtd.TRACKED_MAX_AGE + 5, path=p) == 0


def test_running_since_follows_the_running_task_precedence(tmp_path, monkeypatch):
    now = time.time()
    f = tmp_path / "dtd-running-hint"
    f.write_text(f"hiit\t{int(now) - 300}\tT1")
    monkeypatch.setattr(dtd, "RUNNING_HINT", f)
    fake = type("M", (), {"observed": staticmethod(lambda now=None: None)})
    monkeypatch.setitem(sys.modules, "running_timer", fake)
    assert dtd._running_since(now) == int(now) - 300                        # the hint's own timestamp
    fake.observed = staticmethod(lambda now=None: {"ts": time.time(), "desc": "hiit", "started": 1234, "task_id": "T1"})
    assert dtd._running_since(now) == 1234                                  # a newer observation wins
    fake.observed = staticmethod(lambda now=None: {"ts": time.time(), "desc": "", "started": 0, "task_id": ""})
    assert dtd._running_since(now) == 0                                     # which may say nothing is running


def test_the_web_prompt_uses_the_fields():
    src = (Path(__file__).parent / "dtd.py").read_text()
    assert "t.running && t.runningSince" in src and "prefillBaseSecs" in src
