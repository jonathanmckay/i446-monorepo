"""Regression (2026-07-21): the habit strip took ~2 minutes to load — the 0n
fetch was ~580 individual per-cell AppleEvents over ssh (per-row date loop to
r500 plus per-cell header/value reads). It must use bulk range reads (~6
AppleEvents, ~1.5s). Same fix pattern as the ص skill's row lookup.

Also: the strip showed only 0neon habits — the current block's five -1neon
rituals now lead both rows, read from the LOCAL build order."""
import datetime as dt
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_strip", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_strip"] = mod
    spec.loader.exec_module(mod)
    return mod


def _fetch_src():
    src = (HERE / "janus.py").read_text()
    m = re.search(r"def fetch_habits_today\(\):(.*?)\ndef ", src, re.DOTALL)
    return m.group(1)


def test_fetch_uses_bulk_range_reads_not_per_cell_loops():
    body = _fetch_src()
    assert 'value of range "C3:C500"' in body, "date lookup must be one bulk read"
    assert 'value of range "D1:AS1"' in body, "headers must be one bulk read"
    assert "value of cell c of row" not in body, "no per-cell read loops"
    assert "repeat with r from 3 to 500" not in body, "no per-row date loop"


def test_fetch_unwraps_ranges_via_local_temporary():
    """`item 1 of (value of range ... of ws)` compiles as an element specifier
    dispatched to Excel and errors (-2753 observed live); the unwrap must go
    through a local temporary."""
    body = _fetch_src()
    assert re.search(r"set tmp\w+ to value of range", body)
    code = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("--") and not l.strip().startswith("#"))
    assert "item 1 of (value of range" not in code


def test_current_block_rituals_split_stamped_vs_pending(tmp_path, monkeypatch):
    mod = _load_tui()
    bo = tmp_path / "build-order.md"
    bo.write_text(
        "## 0₲\n- [ ] x\n\n## -1₲\n\n- 卯\n    - [ ]\n- 巳 ☀️ 📧\n    - [ ]\n- 午\n    - [ ]\n")
    monkeypatch.setattr(mod, "BUILD_ORDER", bo)
    monkeypatch.setattr(mod, "view_now",
                        lambda: dt.datetime(2026, 7, 21, 9, 0, tzinfo=mod.TZ))
    mod.STATE.day_offset = 0
    done, pending = mod._current_block_rituals()
    assert "☀️" in done and "📧" in done
    assert "🎯" in pending and "⏱️" in pending and "✅" in pending
    assert "😈" not in done + pending


def test_ritual_chips_lead_both_strip_rows(monkeypatch):
    mod = _load_tui()
    monkeypatch.setattr(mod, "_current_block_rituals", lambda: (["☀️"], ["🎯"]))
    mod.STATE.habits_today = [("0l", 1.0), ("hiit", None)]
    mod.STATE.habits_ytd = {}
    frags = [t for _s, t in mod.render_habits_today()]
    text = "".join(frags)
    row_done, row_pending = text.rstrip("\n").split("\n")
    assert row_done.startswith("☀️") and "1" in row_done
    assert row_pending.startswith("🎯") and "hiit" in row_pending


def test_past_day_view_shows_no_ritual_chips():
    mod = _load_tui()
    mod.STATE.day_offset = -1
    assert mod._current_block_rituals() == ([], [])
    mod.STATE.day_offset = 0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
