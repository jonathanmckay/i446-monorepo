"""Regression (2026-11-05): "tried to record xk88 and it said 'already recorded'
if that's the case then it should show me the points!". A did-fast crash still
marked the row's command done, so the retry was refused with nothing credited."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_janus_points import _load_tui  # noqa: E402


def test_crash_does_not_count_as_recorded():
    m = _load_tui()
    assert not m._did_may_have_credited("UnboundLocalError: local variable", None)
    assert not m._did_may_have_credited("err: boom", None)


def test_success_counts_and_a_timeout_stays_guarded():
    m = _load_tui()
    assert m._did_may_have_credited("✓ xk88 +7分", {"results": [{"name": "xk88"}]})
    # The points may have landed: a second run could double-credit.
    assert m._did_may_have_credited("err: did-fast timed out, points may have landed: check before redoing", None)


def test_refused_run_does_not_count(monkeypatch):
    m = _load_tui()
    monkeypatch.setattr(m.janus_done, "refusal", lambda d: "refused")
    assert not m._did_may_have_credited("refused", {"results": []})


def test_already_recorded_message_shows_the_points(monkeypatch):
    m = _load_tui()
    monkeypatch.setattr(m, "_row_points", lambda d, mins, ids: (True, 7))
    assert m._already_recorded_msg("xk88 1915-1922", "xk88", 7, [1]) == \
        "already recorded [7]: xk88 1915-1922, not re-running"
    monkeypatch.setattr(m, "_row_points", lambda d, mins, ids: (False, 0))
    assert "no 分 on record" in m._already_recorded_msg("xk88 1915-1922", "xk88", 7, [1])


def test_xk88_row_shows_the_minutes_neon_credited_not_its_row2_weight():
    # 0n!AZ is SUM(AJ:AO): 7m earned 7 分, while the row showed the weight, 30.
    from neon.rates import zero_n_points
    assert zero_n_points("xk88", 7) == 7
