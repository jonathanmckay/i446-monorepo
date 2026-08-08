"""User requests 2026-07-28 (follow-ups to the value-tag work):

1. Free rows right-justify with the calendar column (tracked reality keeps
   the left edge; the plan and free time share the right).
2. Value tags worth points (#-1/#-2/#-3) show on entry rows, right side,
   just before the minutes.
3. ^P splits the selected completed entry in two at a chosen HHMM (midpoint
   prefilled), matching dtd's ctrl-p split binding.
"""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")
_KEY_ALIASES = {"enter": "c-m"}


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_split", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_split"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeApp:
    def create_background_task(self, coro):
        coro.close()

    def invalidate(self):
        pass


class _FakeEvent:
    app = _FakeApp()


def _binding(mod, key):
    canon = _KEY_ALIASES.get(key, key)
    hits = [b for b in mod.kb.bindings if b.keys == (canon,)]
    assert hits, f"no binding for {key!r}"
    return hits[0]


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def _entry_item(today, dur=30, ids=(1,), running=False, desc="eat"):
    return {"kind": "entry", "start_dt": today.replace(hour=12),
            "entry_ids": list(ids), "raw_desc": desc, "project_id": None,
            "dur_min": dur, "running": running}


# ─── free rows right-justified ──────────────────────────────────────────────

def test_free_row_right_justified():
    mod = _load_tui()
    mod.STATE.events = []
    today = _midnight()
    picks = mod._future_free_gaps(18, 19, today.replace(hour=13))
    text = "".join(t for _, t, *_ in mod._compact_block_lines("戌", 18, picks, 0, "", is_future=True))
    free_line = next(l for l in text.split("\n") if "free →" in l)
    assert free_line.rstrip().endswith(")"), "label (with duration) sits at the right edge"
    assert "┄" in free_line and free_line.index("┄") < free_line.index("free"), \
        f"┄ texture leads, label right: {free_line!r}"


# ─── value tags on entry rows ───────────────────────────────────────────────

def test_value_tags_render_before_minutes():
    mod = _load_tui()
    mod.STATE.events = []
    today = _midnight()
    picks = [{"start_dt": today.replace(hour=12, minute=5), "time_str": "12:05",
              "label": "eat · hcb", "style": "", "dur_min": 25,
              "entry_ids": [1], "raw_desc": "eat", "project_id": None,
              "tags": ["-2", "billable"]}]
    text = "".join(t for _, t, *_ in mod._compact_block_lines("未", 12, picks, 0, ""))
    row = next(l for l in text.split("\n") if "eat" in l)
    assert "#-2" in row
    assert row.index("#-2") > row.index("eat")
    assert row.index("#-2") < row.index("25m"), "tag sits before the minutes"
    assert "#billable" not in row, "only VALUE_TAGS render"


def test_merged_entries_union_tags():
    """Contiguous same-desc entries merge; their tags must union so a tag on
    either half still shows on the merged row."""
    mod = _load_tui()
    src = (HERE / "janus.py").read_text()
    assert src.count('merged[-1]["tags"] = sorted(set(merged[-1].get("tags") or [])') == 2, \
        "both merged builders (render_morning + _current_block_lines) union tags"


# ─── ^P split ───────────────────────────────────────────────────────────────

def _setup(mod):
    mod.STATE.events = []
    mod.STATE.entries = []
    mod.STATE.entries_yday = []
    mod.STATE.split_target = None
    mod.STATE.edit_target = None
    mod.input_buffer.text = ""


def test_ctrl_p_arms_split_with_midpoint_prefill():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    item = _entry_item(today, dur=30)
    mod.STATE.visible_events = [item]
    mod.STATE.event_sel = mod._sel_key(item)
    _binding(mod, "c-p").handler(_FakeEvent())
    assert mod.STATE.split_target is not None
    assert mod.input_buffer.text == "1215", "midpoint of 1200-1230 prefilled"
    assert mod.STATE.split_target["end_dt"] == today.replace(hour=12, minute=30)


def test_ctrl_p_refuses_running_and_merged_rows():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    for bad in (_entry_item(today, running=True), _entry_item(today, ids=(1, 2))):
        mod.STATE.visible_events = [bad]
        mod.STATE.event_sel = mod._sel_key(bad)
        _binding(mod, "c-p").handler(_FakeEvent())
        assert mod.STATE.split_target is None


def test_split_submit_validates_cut_inside_window():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    mod.STATE.split_target = {
        "id": 1, "date": today.date(), "desc": "eat", "project_id": None,
        "tags": [], "start_dt": today.replace(hour=12),
        "end_dt": today.replace(hour=12, minute=30),
    }
    calls = []
    mod.toggl_api.update_entry = lambda *a, **k: calls.append(("update", a, k))
    mod.toggl_api.create_entry = lambda *a, **k: calls.append(("create", a, k))
    mod.input_buffer.text = "1300"  # outside the window
    _binding(mod, "enter").handler(_FakeEvent())
    assert mod.STATE.split_target is None
    assert calls == [], "an out-of-window cut must not touch Toggl"
    assert "inside" in mod.STATE.flash


def test_split_submit_empty_cancels():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    mod.STATE.split_target = {
        "id": 1, "date": today.date(), "desc": "eat", "project_id": None,
        "tags": [], "start_dt": today.replace(hour=12),
        "end_dt": today.replace(hour=12, minute=30),
    }
    mod.input_buffer.text = ""
    _binding(mod, "enter").handler(_FakeEvent())
    assert mod.STATE.split_target is None
    assert "cancelled" in mod.STATE.flash


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
