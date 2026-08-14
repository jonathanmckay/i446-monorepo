"""Regression: neon_blocks.score_day() must not credit points for blocks that
haven't started yet — the same "-1n on janus and neon do not match" bug
tools/tg/janus.py fixed for its display chip (see
tools/tg/test_janus_future_block_emojis.py) and lib/blocks.py's is_future_block
docstring says every reader must apply. score_day was the one reader left
unguarded: tools/did/did-fast.py's run_ritual calls it with no time gate to
compute the value it writes IMMEDIATELY to 0分!P on every ritual completion,
so a build order with a pre-stamped/auto-awarded future block made Neon's
live P total run ahead of what Janus showed for the same moment."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
TZ = ZoneInfo("America/Los_Angeles")


def _load():
    spec = importlib.util.spec_from_file_location("neon_blocks_future", HERE / "neon_blocks.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["neon_blocks_future"] = mod
    spec.loader.exec_module(mod)
    return mod


# Same fixture as test_janus_future_block_emojis.py's _BUILD_ORDER: every
# block pre-stamped with prayer + done + goal icons, including ones well
# ahead of "now".
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


def test_future_blocks_are_excluded_from_score_day():
    mod = _load()
    # 11:30 → current block is 午 (10-11). 未 (12-13) and 亥 (20-21) are future.
    now = dtm.datetime.now(TZ).replace(hour=11, minute=30, second=0, microsecond=0)
    parts, total, formula = mod.score_day(_BUILD_ORDER, now=now)

    scored = {block for block, _pts in parts}
    assert "巳" in scored and "午" in scored, "started blocks must still score"
    assert "未" not in scored, "future block 未 must not score"
    assert "亥" not in scored, "future block 亥 must not score"

    # 卯 (☀️✅=4) + 巳 (☀️✅🎯=7) + 午 (☀️✅🎯=7) = 18; 未/亥 excluded.
    assert total == 18
    assert formula == "=4+7+7"


def test_default_now_excludes_future_blocks_too():
    """No explicit `now` (did-fast.py's real call signature) must still gate
    on the live clock, not trust every stamped header unconditionally."""
    mod = _load()
    # A block dated far in the future relative to any real "now" this test
    # will ever run at is guaranteed future — 亥 (20-21) pre-stamped alone.
    text = "## -1₲\n\n- 亥 ☀️ 🎯\n    - [ ] habits\n"
    parts, total, formula = mod.score_day(text)
    assert parts == []
    assert total == 0
    assert formula == "=0"


def test_in_progress_block_still_scores_at_its_start_hour():
    mod = _load()
    # 10:00 sharp → 午 (10-11) has just started; it must score, 未 stays future.
    now = dtm.datetime.now(TZ).replace(hour=10, minute=0, second=0, microsecond=0)
    parts, total, formula = mod.score_day(_BUILD_ORDER, now=now)
    scored = {block for block, _pts in parts}
    assert "午" in scored, "block scores the moment its start hour arrives"
    assert "未" not in scored


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
