"""Feature (2026-07-21): minimum-commitment habits (o314/冥想/其他人) render as
YTD-standing ±N chips — the same numbers as the jm dashboard "2026" header
cards — instead of daily done/pending chips ("not about doing every day per
se, but holding up a minimum commitment")."""
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_ytd", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_ytd"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_ytd_cells_match_dashboard_cards():
    """The cells janus reads must stay the ones the dashboard's 2026 header
    cards read (CACHE_CARDS in tools/personal-dashboard/dashboard.py)."""
    mod = _load_tui()
    dash = (HERE.parent / "personal-dashboard" / "dashboard.py").read_text()
    for label, cell in mod.HABIT_YTD_CELLS.items():
        col, row = re.match(r"([A-Z]+)(\d+)", cell).groups()
        assert re.search(
            r'"label":\s*"%s",\s*"col":\s*"%s",\s*"row":\s*%s,\s*"period":\s*"2026"'
            % (re.escape(label), col, row), dash), \
            f"{label} → {cell} not found as a 2026 card in dashboard.py"


def test_render_ytd_chips_signed_and_colored():
    mod = _load_tui()
    mod.STATE.habits_today = [("0l", 1.0)]
    mod.STATE.habits_ytd = {"o314": -107.0, "其他人": 1.0}
    frags = mod.render_habits_today()
    behind = next((s, t) for s, t in frags if "o314" in t)
    ahead = next((s, t) for s, t in frags if "其他人" in t)
    assert "-107" in behind[1] and "#b3261e" in behind[0]
    assert "+1" in ahead[1] and "#2e7d32" in ahead[0]


def test_fetch_filter_drops_ytd_habits_from_daily_rows():
    """The fetch loop must skip YTD habits so they never show as daily
    done/pending chips (structural — the loop's skip condition)."""
    mod = _load_tui()
    src = (HERE / "janus.py").read_text()
    assert "name.lower() in HABIT_YTD_CELLS" in src
    for name in ("o314", "冥想", "其他人"):
        assert name in mod.HABIT_YTD_CELLS


def test_ytd_chips_render_even_with_no_daily_habits():
    mod = _load_tui()
    mod.STATE.habits_today = []
    mod.STATE.habits_ytd = {"冥想": -80.0}
    frags = mod.render_habits_today()
    assert any("冥想 -80" in t for _s, t in frags)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
