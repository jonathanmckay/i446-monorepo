"""Regression tests for tg-tui sharing dtd's abbreviated (Haiku) task names.

dtd shows a `short` label per task; tg-tui should render the same label so a
timer reads identically in both. The Toggl description is the task content with
(N)/[N]/{N} annotations stripped, so display_desc maps a normalized description
to the cleaned short name, falling back to the description when none exists.
"""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("tg_tui_short", HERE / "tg-tui.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_short"] = m
    spec.loader.exec_module(m)
    return m


def test_clean_annotations_strips_time_points_estimate_and_tag():
    m = _load()
    assert m._clean_annotations("foo (30) [40]") == "foo"
    assert m._clean_annotations("bar {60}") == "bar"
    assert m._clean_annotations("baz (20)[40]") == "baz"
    assert m._clean_annotations("qux @g245") == "qux"


def test_display_desc_maps_content_to_short(tmp_path, monkeypatch):
    m = _load()
    cache = tmp_path / "task-queue.json"
    cache.write_text(json.dumps({
        "today": [
            {"content": "qz12-hedge-01: Open Schwab/Fidelity research panel + screen ETFs (20)[40]",
             "short": "qz12-hedge-01: Schwab/Fidelity (20) [40]"},
            {"content": "clear out email queue {60}"},  # no short → falls back
        ],
    }))
    monkeypatch.setattr(m, "TASK_QUEUE", cache)
    m.fetch_short_names()
    # The Toggl description is the content with annotations stripped.
    assert m.display_desc("qz12-hedge-01: Open Schwab/Fidelity research panel + screen ETFs") \
        == "qz12-hedge-01: Schwab/Fidelity"
    # No short on file → unchanged.
    assert m.display_desc("clear out email queue") == "clear out email queue"
    # Unknown description → unchanged; blank stays blank.
    assert m.display_desc("ad-hoc habit") == "ad-hoc habit"
    assert m.display_desc("") == ""


def test_norm_key_tolerates_dash_and_case_drift(tmp_path, monkeypatch):
    """The timer name may use a plain hyphen where the content has an em-dash,
    and differ in case — the lookup must still match."""
    m = _load()
    cache = tmp_path / "task-queue.json"
    cache.write_text(json.dumps({
        "today": [{"content": "habig-02 — read reference docs (30) [40]",
                   "short": "habig-02 — read docs (30) [40]"}],
    }))
    monkeypatch.setattr(m, "TASK_QUEUE", cache)
    m.fetch_short_names()
    assert m.display_desc("habig-02 - read reference docs") == "habig-02 — read docs"


def test_missing_cache_is_safe(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "TASK_QUEUE", tmp_path / "nope.json")
    m.SHORT_NAMES.clear()
    m.fetch_short_names()  # must not raise
    assert m.display_desc("anything") == "anything"
