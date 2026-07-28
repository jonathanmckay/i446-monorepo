"""Compact-block time-column layout (after the 2026-06-27 redesign): full-time
rows have NO leading space (start at column 0); minutes-only continuation rows
are indented two so the colon aligns under the full-time colon. The old
single-leading-space alignment with the detail band was dropped on purpose."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_indent", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_indent"] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry(desc, start, end, pid=None):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": pid, "running": False, "id": 1}


def test_compact_full_time_rows_have_no_leading_space():
    """A full-time body row (hour rolled over) starts at column 0 — the 1-space
    left pad was removed."""
    mod = _load_tui()
    today = dt.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    # 辰 (06-08): deep work 06:00, quick task 07:30. The 07:30 row rolls the hour
    # over, so it renders as a full `07:30` at column 0.
    mod.STATE.entries = [
        _entry("deep work", today.replace(hour=6), today.replace(hour=7, minute=30)),
        _entry("quick task", today.replace(hour=7, minute=30), today.replace(hour=7, minute=50)),
    ]
    mod.STATE.entries_yday = []
    mod.STATE.block_points = {}
    mod.STATE.events = []
    mod.detail_window = lambda: (today.replace(hour=12), today.replace(hour=16))
    text = "".join(t for _, t in mod.render_morning())
    rows = [ln for ln in text.split("\n") if "quick task" in ln]
    assert rows, "expected a body row with the entry"
    assert rows[0].startswith("07:30"), f"full-time row at col 0, got {rows[0]!r}"
    assert not rows[0].startswith(" "), f"no leading space, got {rows[0]!r}"


def test_minutes_only_rows_align_colon_under_full_time():
    """A same-hour continuation row is `  :MM` (two-space indent) so its colon
    sits under the colon of a full `HH:MM` row above."""
    mod = _load_tui()
    today = dt.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    # 辰: deep work 06:00 then nothing — body fills 06:30 / 07:00 marks. 06:30 is
    # same hour as the header's 06, so it abbreviates to `  :30`.
    mod.STATE.entries = [
        _entry("deep work", today.replace(hour=6), today.replace(hour=6, minute=20)),
    ]
    mod.STATE.entries_yday = []
    mod.STATE.block_points = {}
    mod.STATE.events = []
    mod.detail_window = lambda: (today.replace(hour=12), today.replace(hour=16))
    text = "".join(t for _, t in mod.render_morning())
    min_rows = [ln for ln in text.split("\n") if ln.startswith("  :")]
    assert min_rows, "expected at least one minutes-only continuation row"
    for ln in min_rows:
        assert ln[2] == ":", f"colon must sit at column 2, got {ln!r}"
        full = [l for l in text.split("\n") if len(l) > 2 and l[:2].isdigit() and l[2] == ":"]
        # Any full HH:MM row also carries its colon at column 2 → aligned.
        for f in full:
            assert f[2] == ":"
