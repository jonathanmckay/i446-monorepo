#!/usr/bin/env python3
"""did-fast's time-range Toggl entries must trim/split any existing entry
(completed or the currently-running one) that overlaps the new range,
instead of blindly creating on top of it.

Bug (2026-07-16): manually backfilling "asha" (09:30-10:00) then "asha prep"
(09:30-10:30) via /did double-counted both time AND points over the shared
09:30-10:00 window -- did-fast's Toggl-entry creation had no overlap
handling at all.

The actual overlap-cleanup ALGORITHM now lives in toggl_api.trim_range
(mcp/toggl_server/toggl_api.py, exhaustively tested in
test_toggl_api_trim_range.py) -- promoted out of did-fast.py 2026-07-19 once
janus.py's entry-edit-to-a-new-time feature needed the identical logic.
_trim_toggl_range here is now a thin delegate; this file only checks the
delegation wiring, not the algorithm itself.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("did_fast_trim", HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_trim"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def df():
    return _load()


def test_trim_toggl_range_delegates_to_shared_toggl_api(df, monkeypatch):
    calls = []

    class _FakeToggl:
        def trim_range(self, start_dt, end_dt, exclude_ids=None):
            calls.append((start_dt, end_dt, exclude_ids))
            return ["Trimmed: something"]

    monkeypatch.setattr(df, "_toggl_api", lambda: _FakeToggl())
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Los_Angeles")
    start = datetime(2026, 7, 19, 9, 30, tzinfo=tz)
    end = datetime(2026, 7, 19, 10, 0, tzinfo=tz)
    out = df._trim_toggl_range(start, end)
    assert calls == [(start, end, None)]
    assert out == ["Trimmed: something"]


def test_create_toggl_calls_trim_before_creating():
    """Structural: _create_toggl (the actual Step 5.5/6 write path used by
    every time-range /did item) must call _trim_toggl_range, not just the new
    helper existing in isolation -- else the fix never fires in practice."""
    src = (HERE / "did-fast.py").read_text()
    i_def = src.index("def _create_toggl(args):")
    body = src[i_def:src.index("\n        with ThreadPoolExecutor", i_def)]
    assert "_trim_toggl_range(" in body


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
