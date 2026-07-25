"""Tests for edit-fast.py — dtd ctrl-g unified edit (name / domain / points)."""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("edit_fast", HERE / "edit-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["edit_fast"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── parse_edits: @code → domain, standalone int → points, rest → name ──

def test_parse_all_three():
    mod = _load()
    assert mod.parse_edits("fix bug 30 @i9") == ("fix bug", "i9", 30)


def test_parse_domain_only():
    mod = _load()
    assert mod.parse_edits("@m5x2") == (None, "m5x2", None)


def test_parse_points_only():
    mod = _load()
    assert mod.parse_edits("30") == (None, None, 30)


def test_parse_name_only():
    mod = _load()
    assert mod.parse_edits("rename this task") == ("rename this task", None, None)


def test_parse_standalone_number_in_name_becomes_points():
    # The accepted tradeoff: any standalone number is points, even mid-name.
    mod = _load()
    assert mod.parse_edits("review 2026 budget") == ("review budget", None, 2026)


def test_parse_last_number_wins_earlier_demoted_to_name():
    mod = _load()
    assert mod.parse_edits("foo 30 40") == ("foo 30", None, 40)


def test_parse_empty():
    mod = _load()
    assert mod.parse_edits("   ") == (None, None, None)


# ── bracketed points + retyped annotations must not duplicate ──
# Bug 2026-07-24: ctrl-g "Matt Booty Instagram photos [20]" on a task
# "…photos (15) [15]" produced "…photos [20] (15) [15]" — the display-syntax
# [20] was treated as name text and the old tail appended after it.

def test_parse_bracketed_points_token():
    mod = _load()
    assert mod.parse_edits("[20]") == (None, None, 20)


def test_parse_name_with_bracketed_points():
    mod = _load()
    assert mod.parse_edits("Matt Booty Instagram photos [20]") == \
        ("Matt Booty Instagram photos", None, 20)


def test_full_line_retype_replaces_points_without_duplicating():
    mod = _load()
    orig = "see Matt booty Instagram photos (15) [15]"
    name, _dom, pts = mod.parse_edits("Matt Booty Instagram photos [20]")
    out = mod.set_name(orig, name)
    out = mod._pf.set_points(out, pts)
    assert out == "Matt Booty Instagram photos (15) [20]"


def test_set_name_typed_annotation_overrides_same_kind_tail():
    # Retyping the (N) inline replaces the preserved (N); other kinds survive.
    mod = _load()
    assert mod.set_name("call dad (5) [10]", "ring dad (30)") == "ring dad (30) [10]"


def test_set_name_nonnumeric_tail_annotations_survive():
    mod = _load()
    assert mod.set_name("ship it (5) [0G]", "ship it now") == "ship it now (5) [0G]"


# ── set_name preserves trailing annotations ──

def test_set_name_keeps_annotations():
    mod = _load()
    assert mod.set_name("call dad (5) [10]", "ring dad") == "ring dad (5) [10]"


def test_set_name_no_annotations():
    mod = _load()
    assert mod.set_name("call dad", "ring dad") == "ring dad"


# ── main: integration with mocked Todoist API + temp cache ──

def _cache(tmp_path, content="review budget [10]", labels=("i9", "0neon"), short=None):
    task = {"id": "42", "content": content, "labels": list(labels)}
    if short is not None:
        task["short"] = short
    cache = {"0neon": [task]}
    cf = tmp_path / "cache.json"
    cf.write_text(json.dumps(cache))
    return cf


def test_main_rename_clears_stale_short(tmp_path, monkeypatch):
    mod = _load()
    cf = _cache(tmp_path, content="old long name [10]", labels=("i9",), short="old shrt")
    mod._df._api = lambda *a, **k: None
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "old long name", "brand new name", str(cf)])
    assert mod.main() == 0
    task = json.loads(cf.read_text())["0neon"][0]
    assert task["content"] == "brand new name [10]"
    assert "short" not in task, "stale Haiku short name must be cleared on rename"


def test_main_applies_name_domain_points(tmp_path, monkeypatch):
    mod = _load()
    cf = _cache(tmp_path, content="old name (5) [10]", labels=("i9", "0neon"))
    calls = []
    mod._df._api = lambda method, path, body=None, **k: calls.append((path, body))

    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "old name", "new name 30 @m5x2", str(cf)])
    assert mod.main() == 0

    bodies = {tuple(b.keys())[0]: b for _, b in calls}
    # content call: name replaced, time annotation kept, points updated
    assert bodies["content"]["content"] == "new name (5) [30]"
    # labels call: domain swapped, bookkeeping preserved
    assert set(bodies["labels"]["labels"]) == {"0neon", "m5x2"}

    patched = json.loads(cf.read_text())["0neon"][0]
    assert patched["content"] == "new name (5) [30]"
    assert patched["labels"] == ["0neon", "m5x2"]


def test_main_points_only_keeps_name(tmp_path, monkeypatch):
    mod = _load()
    cf = _cache(tmp_path, content="call dad (5) [10]", labels=("i9",))
    calls = []
    mod._df._api = lambda method, path, body=None, **k: calls.append((path, body))
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "call dad", "25", str(cf)])
    assert mod.main() == 0
    assert len(calls) == 1 and calls[0][0] == "/tasks/42"
    assert calls[0][1]["content"] == "call dad (5) [25]"


def test_main_rejects_unknown_domain(tmp_path, monkeypatch):
    mod = _load()
    cf = _cache(tmp_path)
    called = []
    mod._df._api = lambda *a, **k: called.append(a)
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "review budget", "@nope", str(cf)])
    assert mod.main() == 1
    assert not called, "invalid domain must not touch Todoist"


def test_main_empty_edit_is_noop(tmp_path, monkeypatch):
    mod = _load()
    cf = _cache(tmp_path)
    called = []
    mod._df._api = lambda *a, **k: called.append(a)
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "review budget", "   ", str(cf)])
    assert mod.main() == 1
    assert not called
