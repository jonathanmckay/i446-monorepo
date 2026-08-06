"""User report 2026-07-27 (follow-up to the near-now Tab fix): "I still
don't see any of the running in 亥 — the title is missing." The day's run
was two Toggl entries, 19:37-19:59 and 19:59-21:00 @hcbp. Two distinct
holes hid them:

1. Picks are assigned to an entry's START block only, so the 19:59 run's
   20:00-21:00 portion rendered in 亥 as anonymous ◇ │ continuation marks
   with no title. Only 睡觉 had spillover handling (_block_sleep_item).
   _block_spill_items generalizes it: any entry crossing the block start
   gets a clipped, titled row carrying the real entry id (selectable, and
   ⌥↵ can grant the spilled portion's points).

2. The over-full-block row cap kept the chronologically-FIRST max_rows
   items, silently dropping the merged 23m run from 戌's 3-row card in
   favor of two sub-10m entries. The cap now keeps the important rows
   (running first, then by duration), re-sorted chronologically."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_spill", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_spill"] = mod
    spec.loader.exec_module(mod)
    return mod


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def _setup(mod):
    mod.STATE.day_offset = 0
    mod.STATE.current_known = True
    mod.STATE.entries_known = True
    mod.STATE.current = None
    mod.STATE.block_points = {}
    mod.STATE.events = []
    mod.STATE.entries = []


def _entry(desc, start, end, pid=1, eid=1, running=False):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": pid, "running": running, "id": eid}


def test_spill_item_clipped_titled_and_selectable():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    mod.STATE.entries = [_entry("run", today.replace(hour=19, minute=59),
                                today.replace(hour=21, minute=0), eid=42)]
    cutoff = today.replace(hour=21, minute=20)
    items = mod._block_spill_items(20, 21, cutoff)  # 亥
    assert len(items) == 1
    it = items[0]
    assert it["start_dt"] == today.replace(hour=20)
    assert it["dur_min"] == 60
    assert "run" in it["label"]
    assert it["entry_ids"] == [42], "must carry the real id for selection/⌥↵"


def test_spill_excludes_sleep_and_non_crossing_entries():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    mod.STATE.entries = [
        _entry("睡觉", today.replace(hour=19), today.replace(hour=23), eid=1),
        _entry("inside", today.replace(hour=20, minute=5),
               today.replace(hour=20, minute=30), eid=2),
    ]
    assert mod._block_spill_items(20, 21, today.replace(hour=21)) == []


def test_current_block_shows_spilled_run_with_title():
    """End-to-end repro of the report: the 19:59-21:00 run must render a
    titled row in 亥's focus card, not just ◇ │ marks."""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    now = today.replace(hour=21, minute=20)
    mod.view_now = lambda: now
    mod.STATE.entries = [
        _entry("run", today.replace(hour=19, minute=37),
               today.replace(hour=19, minute=59), eid=41),
        _entry("run", today.replace(hour=19, minute=59),
               today.replace(hour=21, minute=0), eid=42),
        _entry("snack", today.replace(hour=21, minute=1),
               today.replace(hour=21, minute=15), eid=43),
    ]
    text = "".join(t for _, t in mod.render_focus_compact())
    hai = text.split("子:00")[0]
    assert "run" in hai, f"spilled run must be titled in 亥:\n{hai}"
    spilled = [it for it in mod.STATE.visible_events
               if isinstance(it, dict) and it.get("kind") == "entry"
               and it.get("entry_ids") == [42]]
    assert spilled, "the spilled row must be selectable"


def test_row_cap_keeps_biggest_not_earliest():
    """戌's 3-row card: five entries where the LATEST is the second-biggest —
    the cap must drop the smallest of the BODY candidates, not the latest.

    2026-08-06: the block's chronologically-first entry (backboard) now
    always rides the header (see test_janus_compact_blocks.py's widened
    header-promotion rule), so it's no longer a candidate the row cap can
    drop at all — it's guaranteed visible regardless of size. This test
    adds a second small entry (tiny, 2m) that ISN'T first, so there's still
    a genuine cap decision among the remaining body candidates: 4 of them
    for 3 slots, and the cap must drop the smallest of those four (tiny),
    not the latest (run)."""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    picks = []
    for name, h, m, dur, eid in [("backboard", 18, 18, 7, 1), ("1 f694", 18, 25, 8, 2),
                                 ("tiny", 18, 30, 2, 5),
                                 ("figure out space", 18, 38, 51, 3), ("run", 19, 37, 23, 4)]:
        picks.append({"start_dt": today.replace(hour=h, minute=m),
                      "time_str": f"{h}:{m:02d}", "label": name, "style": "",
                      "dur_min": dur, "entry_ids": [eid], "raw_desc": name,
                      "project_id": None})
    frags = mod._compact_block_lines("戌", 18, picks, 0, "")
    text = "".join(t for _, t in frags)
    assert "backboard" in text.split("\n")[0], "the chronologically-first entry rides the header"
    assert "run" in text and "figure out space" in text and "1 f694" in text
    assert "tiny" not in text, "the smallest of the BODY candidates is the one to drop"


def test_row_cap_never_drops_the_running_entry():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    picks = [{"start_dt": today.replace(hour=18, minute=5 * i),
              "time_str": "", "label": f"e{i}", "style": "",
              "dur_min": 30 + i, "entry_ids": [i], "raw_desc": f"e{i}",
              "project_id": None} for i in range(4)]
    picks.append({"start_dt": today.replace(hour=19, minute=50), "time_str": "",
                  "label": "live", "style": "", "dur_min": 1, "is_running": True,
                  "entry_ids": [9], "raw_desc": "live", "project_id": None})
    text = "".join(t for _, t in mod._compact_block_lines("戌", 18, picks, 0, ""))
    assert "live" in text, "the running row survives the cap regardless of duration"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
