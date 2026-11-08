"""Regression test for the AppFolio work-order open/closed classifier.

Guards against the v162 false-zero bug: the intake reported total_open=0
because its substring filter ("open"/"in progress"/"pending") matched none of
AppFolio's real open statuses (New/Scheduled/Assigned/Estimated/...).

Run: python3 scripts/test_dream_intake_wo.py   (also collectible by pytest)
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "dream_intake", Path(__file__).with_name("dream-intake.py")
)
_di = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_di)


def test_real_open_statuses_count_as_open():
    # The exact enum AppFolio returned on 2026-10-19 for non-terminal WOs.
    for status in ("New", "Scheduled", "Assigned", "Estimated",
                   "In Progress", "On Hold"):
        assert _di._wo_is_open(status) is True, status


def test_terminal_statuses_count_as_closed():
    for status in ("Completed", "Complete", "Canceled", "Cancelled",
                   "Closed", "completed", "  CLOSED  "):
        assert _di._wo_is_open(status) is False, status


def test_blank_or_missing_status_is_not_open():
    for status in ("", "   ", None):
        assert _di._wo_is_open(status) is False, repr(status)


def test_old_substring_filter_would_have_failed():
    # Documents the bug: none of the real open statuses contain the old
    # substrings, which is why the pre-v162 filter reported zero.
    old = lambda s: ("open" in s.lower() or "in progress" in s.lower()
                     or "pending" in s.lower())
    assert old("New") is False
    assert old("Scheduled") is False
    assert old("Assigned") is False
    assert old("Estimated") is False
    # new classifier gets them right
    assert all(_di._wo_is_open(s) for s in ("New", "Scheduled", "Assigned", "Estimated"))


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
