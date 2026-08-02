"""toggl_project_code: a literal "m5x2" in a Toggl entry's description must
win over its (missing or wrong) project_id, mirroring gcal_project_code's
2026-07-12 title-override fix — see test_janus_gcal_project_code.py.

Bug (2026-08-02): "m5x2 Strat" rendered with no color (not m5x2-crimson) in
the block view after a Toggl continuation/restart dropped its @m5x2 project
(project_id came back None/0). project_style()/proj_code() only resolve by
project_id — unlike gcal_project_code, they never looked at the description
text, so an entry that says "m5x2" right in its name still rendered
uncolored whenever Toggl itself lost the project tag."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_togglcode", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_togglcode"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_m5x2_title_wins_with_no_project_id():
    mod = _load_tui()
    assert mod.toggl_project_code(None, "m5x2 Strat") == "m5x2"


def test_m5x2_title_wins_over_wrong_project_id():
    """Even if project_id resolves to some OTHER code, the literal title
    still wins — same priority gcal_project_code gives title over its
    calendar-level default."""
    mod = _load_tui()
    other_pid = next(iter(mod.PROJECT_CODE), None)
    assert mod.toggl_project_code(other_pid, "m5x2 Strat") == "m5x2"


def test_no_m5x2_in_title_falls_back_to_project_id():
    mod = _load_tui()
    assert mod.toggl_project_code(None, "standup") == ""


def test_past_block_picks_colors_m5x2_entry_with_no_project():
    """Integration: the exact reported symptom — a finished "m5x2 Strat"
    entry with project_id=None, as seen in the compact past-block view."""
    mod = _load_tui()
    today = dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    merged = [{
        "start_dt": today.replace(hour=10, minute=43),
        "end_dt": today.replace(hour=11, minute=29),
        "desc": "m5x2 Strat", "project_id": None, "running": False,
        "ids": [1], "tags": [],
    }]
    picks = mod._past_block_picks("巳", merged)
    assert len(picks) == 1
    assert picks[0]["style"] == "fg:#d50032"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
