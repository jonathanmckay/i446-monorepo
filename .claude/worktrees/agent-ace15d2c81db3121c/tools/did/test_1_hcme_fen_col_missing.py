#!/usr/bin/env python3
"""Regression (2026-09-05): "I marked 1 hcme as done, but it didn't give me
the points."

"1 hcme" (the weekly emotional-checkin habit) matched the 1n+ header fine
(col AA in this fixture) but ONENEON_TO_0FEN had no entry for "1 hcme", so
Step 0.2's fen_col lookup fell through to the generic domain-token fallback:
LABEL_TO_0FEN.get("hcme"). "hcme" is not a real project/domain code (only
"hcm" and "hcmc" are), so that lookup also returned None, and fen_col stayed
None -- the 0n+ header write happened, but no points ever reached 0分. Same
bug class as the "1 m5x2" and "s+hcbp" fixes already noted in comments above
ONENEON_TO_0FEN and _project_fen_col.

Fix: add an explicit "1 hcme" -> "V" entry (the hcm/mindfulness column,
matching /tg's own "1 hcme" -> hcm project mapping) to ONENEON_TO_0FEN.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "did_fast_hcme", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_hcme"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def df():
    return _load()


HEADERS = {"0n": {}, "1n": {"1 hcme": "AA"}}


def _tq():
    return {"0neon": [], "夜neon": [], "1neon": [], "today": []}


def test_1_hcme_resolves_a_fen_col(df, monkeypatch):
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: 15)
    items = df.parse_input("1 hcme")
    routes = df.route_items(items, HEADERS, _tq(), skip_todoist=True)
    assert len(routes) == 1
    r = routes[0]
    assert r.step == "1n"
    assert r.fen_col is not None, (
        "1 hcme matched its 1n+ header but resolved no 0分 column -- points "
        "silently never reach 0分 even though the habit shows as completed")
    assert r.fen_col == "V"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
