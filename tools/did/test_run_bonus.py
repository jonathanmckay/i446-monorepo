"""Regression tests for run.py long-session auto-bonus (冥想 / o314).

Covers:
  - `[N]` parsed as explicit minutes by `_parse_input`.
  - `apply_long_bonus` writes cumulatively to 1n+ and literally to 0分.
  - `run_0n` triggers the bonus only when minutes >= threshold and only on today.
  - Non-special habits never get a bonus.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HERE = Path(__file__).parent
sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))

# Import run.py via importlib so the hyphen-free filename works regardless of
# the interpreter's cwd.
_RUN_SPEC = importlib.util.spec_from_file_location("did_run", _HERE / "run.py")
run = importlib.util.module_from_spec(_RUN_SPEC)
sys.modules["did_run"] = run
_RUN_SPEC.loader.exec_module(run)  # type: ignore[union-attr]


# ── _parse_input ────────────────────────────────────────────────────────────

def test_parse_input_bracket_n_is_explicit_minutes():
    q, target, tr, mins = run._parse_input("冥想 [52]")
    assert q == "冥想"
    assert mins == 52
    assert tr is None


def test_parse_input_bare_digit_still_works():
    q, target, tr, mins = run._parse_input("o314 66")
    assert q == "o314"
    assert mins == 66


def test_parse_input_bracket_n_with_date_suffix():
    q, target, tr, mins = run._parse_input("冥想 [52] 4/27")
    assert q == "冥想"
    assert target == "4/27"
    assert mins == 52


def test_parse_input_no_minutes_when_only_bracket_alone():
    # `[52]` alone (no habit name) is not a valid /did invocation; require
    # len(parts) > 1 to extract minutes — guards against single-token misuse.
    q, target, tr, mins = run._parse_input("[52]")
    assert mins is None


# ── apply_long_bonus ────────────────────────────────────────────────────────

class _FakeExcel:
    def __init__(self, read_value="0"):
        self.read_value = read_value
        self.writes = []
        self.appends = []

    def read(self, sheet, col, **kw):
        return {"ok": True, "value": self.read_value}

    def write(self, sheet, col, **kw):
        self.writes.append((sheet, col, kw))
        return {"ok": True, "value": kw.get("value")}

    def append(self, sheet, col, **kw):
        self.appends.append((sheet, col, kw))
        return {"ok": True, "value": kw.get("value")}


def test_apply_long_bonus_cumulative_1n_and_literal_0fen():
    fake = _FakeExcel(read_value="10")
    with patch.object(run, "excel", fake), \
         patch.object(run, "_calc_mw", return_value=(5.1, 27)):
        msg = run.apply_long_bonus("长冥想", 26, "5/4")
    assert msg is not None
    # 1n+ write: cumulative add to existing 10 → 36
    assert len(fake.writes) == 1
    sheet, col, kw = fake.writes[0]
    assert sheet == "1n+"
    assert kw["row"] == 27
    assert kw["value"] == "36"
    # 0分 append: literal +26 (NOT a formula reference)
    assert len(fake.appends) == 1
    sheet0, col0, kw0 = fake.appends[0]
    assert sheet0 == "0分"
    assert kw0["value"] == "+26"
    assert "'1n+'!" not in kw0["value"], "0分 must get literal +pts, not a formula"


def test_apply_long_bonus_handles_blank_existing_cell():
    fake = _FakeExcel(read_value="")
    with patch.object(run, "excel", fake), \
         patch.object(run, "_calc_mw", return_value=(5.1, 27)):
        run.apply_long_bonus("长冥想", 15, "5/4")
    assert fake.writes[0][2]["value"] == "15"


def test_apply_long_bonus_unknown_habit_returns_none():
    fake = _FakeExcel()
    with patch.object(run, "excel", fake):
        msg = run.apply_long_bonus("does-not-exist-habit-xyz", 26, "5/4")
    assert msg is None
    assert fake.writes == []
    assert fake.appends == []


# ── run_0n triggers bonus ───────────────────────────────────────────────────

def _route_for(habit_query: str) -> dict:
    """Run route.py via the in-process module to get a real route dict."""
    import json
    import subprocess
    r = subprocess.run(
        ["python3", str(Path.home() / "i446-monorepo/tools/did/route.py"),
         habit_query, "--target-date", "5/4"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(r.stdout)


def _stub_run_0n(d, minutes, target_date="5/4"):
    """Run run_0n with all side effects mocked. Returns (rc, bonus_calls)."""
    bonus_calls = []

    def fake_apply_long_bonus(long_habit_id, pts, td):
        bonus_calls.append((long_habit_id, pts, td))
        return f"  ⤷ {long_habit_id} +{pts}"

    fake_excel = _FakeExcel()
    with patch.object(run, "excel", fake_excel), \
         patch.object(run, "_find_and_close_todoist", return_value=None), \
         patch.object(run, "_append_completed"), \
         patch.object(run, "_fire_refresh"), \
         patch.object(run, "apply_long_bonus", side_effect=fake_apply_long_bonus), \
         patch.object(run, "_today_md", return_value=target_date):
        # explicit_minutes path → no Toggl auto-detect
        rc = run.run_0n(d, "冥想", target_date, None, explicit_minutes=minutes)
    return rc, bonus_calls


def test_meditation_52min_triggers_bonus_of_26():
    d = _route_for("冥想")
    assert d.get("bonus") is not None, "config: 冥想 must carry a bonus block"
    rc, calls = _stub_run_0n(d, minutes=52)
    assert rc == 0
    assert calls == [("长冥想", 26, "5/4")]


def test_o314_45min_triggers_bonus_of_22():
    d = _route_for("o314")
    assert d.get("bonus") is not None, "config: o314 must carry a bonus block"
    rc, calls = _stub_run_0n(d, minutes=45)
    assert calls == [("long-o314", 22, "5/4")]


def test_meditation_29min_skips_bonus():
    d = _route_for("冥想")
    rc, calls = _stub_run_0n(d, minutes=29)
    assert calls == []


def test_meditation_30min_triggers_bonus_inclusive():
    """Threshold is inclusive: exactly 30 minutes still earns the bonus."""
    d = _route_for("冥想")
    rc, calls = _stub_run_0n(d, minutes=30)
    assert calls == [("长冥想", 15, "5/4")]


def test_non_special_habit_has_no_bonus():
    """A regular 0n habit with a long session must NOT trigger any bonus."""
    d = _route_for("hiit")
    assert d.get("bonus") is None, "hiit must not carry a bonus block"
    rc, calls = _stub_run_0n(d, minutes=60)
    assert calls == []


def test_bonus_skipped_for_past_date_posthoc():
    """run_0n's posthoc branch returns early before the bonus check; ensure
    no bonus is awarded retroactively."""
    d = _route_for("冥想")
    bonus_calls = []

    def fake_apply_long_bonus(*a, **k):
        bonus_calls.append(a)
        return ""

    import todoist as td_mod
    with patch.object(run, "todoist", MagicMock()) as mt, \
         patch.object(run, "_append_completed"), \
         patch.object(run, "apply_long_bonus", side_effect=fake_apply_long_bonus), \
         patch.object(run, "_today_md", return_value="5/4"):
        mt.create_task.return_value = {"id": "x"}
        run.run_0n(d, "冥想", "4/27", None, explicit_minutes=60)
    assert bonus_calls == []
