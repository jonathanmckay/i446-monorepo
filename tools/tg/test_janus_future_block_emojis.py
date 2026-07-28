"""Regression: ritual icons (☀️ prayer, ✅ done, 🎯 goal) must NOT show for
blocks that haven't started yet. The build order pre-stamps future block
headers for the whole day; _read_block_emojis must drop any block whose start
hour is still in the future, keeping the in-progress block."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_future", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_future"] = mod
    spec.loader.exec_module(mod)
    return mod


# Build order with every block pre-stamped with prayer + done + goal icons,
# exactly the forward-looking template that lit future blocks before the fix.
_BUILD_ORDER = """## -1₲

- 卯 ☀️ ✅
    - [ ] wake
- 巳 ☀️ ✅ 🎯
    - [ ] read
- 午 ☀️ ✅ 🎯
    - [ ] lunch
- 未 ☀️ ✅ 🎯
    - [ ] tasks
- 亥 ☀️ 🎯
    - [ ] habits

## next-section
"""


def _run(mod, tmp_path, now):
    bo = tmp_path / "build-order.md"
    bo.write_text(_BUILD_ORDER, encoding="utf-8")
    mod.BUILD_ORDER = bo
    return mod._read_block_emojis(now=now)


def test_future_blocks_have_no_ritual_emojis(tmp_path):
    mod = _load_tui()
    # 11:30 → current block is 午 (10-11). 未 (12-13) and 亥 (20-21) are future.
    now = dtm.datetime.now(TZ).replace(hour=11, minute=30, second=0, microsecond=0)
    result = _run(mod, tmp_path, now)

    # Started/in-progress blocks keep their score label (a bare number since
    # 2026-07-21 — stamped icons render as their summed -1₦ points, not the
    # raw emojis, and the ₦ glyph was dropped for width).
    assert "巳" in result and result["巳"].isdigit()
    assert "午" in result, "in-progress block 午 must keep its score"

    # Future blocks must be absent — no prayer/done/goal for blocks not yet begun.
    assert "未" not in result, "future block 未 must not show ritual icons"
    assert "亥" not in result, "future block 亥 must not show ritual icons"


def test_in_progress_block_kept_at_start_hour(tmp_path):
    mod = _load_tui()
    # 10:00 sharp → 午 (10-11) has just started; it must be kept, 未 still future.
    now = dtm.datetime.now(TZ).replace(hour=10, minute=0, second=0, microsecond=0)
    result = _run(mod, tmp_path, now)
    assert "午" in result, "block kept the moment its start hour arrives"
    assert "未" not in result


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
