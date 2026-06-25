"""Regression tests for /inbound's daily -1₲ reset.

Request (2026-06-25): /inbound should focus on today and auto-clear yesterday's
cards. The daily reset (snapshot_build_order, once per day) now wipes each -1₲
block's goals AND `actual:` log back to an empty `- [ ]` placeholder so
yesterday's goals don't linger — AFTER archiving the day to v_logs.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("_two_n", HERE / "-2n.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


m = _load()

SAMPLE = """\
## 0₲
- [ ] keep this daily goal

## -1₲

- 卯
    - [ ] yesterday morning goal {20}
- 辰 ✅
    - [x] done thing {10}
    - actual:
        - wake up @infra (07:35-07:44, 9m)
        - read @xk87 (08:05-08:46, 40m)
- 亥
    - [ ] wind down {8}

## 以后的目标
- [ ] backlog item
"""


def test_clear_block_goals_resets_each_block(tmp_path, monkeypatch):
    bo = tmp_path / "build-order.md"
    bo.write_text(SAMPLE)
    monkeypatch.setattr(m, "BUILD_ORDER", bo)

    m.clear_block_goals()
    out = bo.read_text()

    # Every -1₲ block is reset to a single empty placeholder.
    assert "yesterday morning goal" not in out
    assert "done thing" not in out
    assert "wind down" not in out
    # The logged actuals are gone too.
    assert "wake up @infra" not in out
    assert "actual:" not in out

    # Headers preserved (incl. any residual marker on 辰 — clear_prayer_markers
    # handles markers separately; clear_block_goals must not drop the header).
    for hdr in ("- 卯", "- 辰", "- 亥"):
        assert any(l.startswith(hdr) for l in out.splitlines()), f"missing header {hdr}"

    # Exactly one empty placeholder per block (3 blocks here).
    assert out.count("    - [ ] ") == 3

    # Sections OUTSIDE -1₲ are untouched.
    assert "- [ ] keep this daily goal" in out      # 0₲
    assert "- [ ] backlog item" in out               # 以后的目标
    # The -1₲ section still ends before 以后的目标.
    assert out.index("## -1₲") < out.index("## 以后的目标")


def test_clear_block_goals_noop_without_section(tmp_path, monkeypatch):
    bo = tmp_path / "build-order.md"
    bo.write_text("## 0₲\n- [ ] only daily goals here\n")
    monkeypatch.setattr(m, "BUILD_ORDER", bo)
    m.clear_block_goals()  # must not raise
    assert bo.read_text() == "## 0₲\n- [ ] only daily goals here\n"


def test_daily_reset_archives_then_wipes_once(tmp_path, monkeypatch):
    """snapshot_build_order: first run of the day archives the FULL build order
    (goals intact) to v_logs, then wipes -1₲. A second run is a no-op (snapshot
    exists) so goals set later today survive."""
    bo = tmp_path / "build-order.md"
    bo.write_text(SAMPLE)
    vlogs = tmp_path / "v_logs"
    monkeypatch.setattr(m, "BUILD_ORDER", bo)
    monkeypatch.setattr(m.Path, "home", staticmethod(lambda: tmp_path))
    # v_logs path in snapshot_build_order is Path.home()/"vault/g245/v_logs";
    # redirect home so the snapshot lands under tmp_path.
    (tmp_path / "vault" / "g245").mkdir(parents=True, exist_ok=True)

    m.snapshot_build_order()

    # Archive captured yesterday's goals BEFORE the wipe.
    snaps = list((tmp_path / "vault/g245/v_logs").glob("*-build-order.md"))
    assert len(snaps) == 1, "expected one archived snapshot"
    archived = snaps[0].read_text()
    assert "yesterday morning goal" in archived, "archive must preserve yesterday's goals"

    # Live build order is now wiped.
    assert "yesterday morning goal" not in bo.read_text()

    # Simulate goals set later today, then a SECOND /inbound run same day.
    live = bo.read_text().replace("    - [ ] \n- 辰", "    - [ ] fresh today goal {5}\n- 辰", 1)
    bo.write_text(live)
    m.snapshot_build_order()  # snapshot already exists → no-op
    assert "fresh today goal" in bo.read_text(), "second run must NOT re-wipe today's goals"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
