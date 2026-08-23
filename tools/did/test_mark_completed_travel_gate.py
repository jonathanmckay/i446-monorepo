"""Regression tests: mark-completed.py's date gate must not wipe or drop
completions on a BACKWARD date move.

Bug: append_names/is_duplicate_today/absorb_remote all gated on exact
date-string equality (`!=`). During travel a machine's locally-computed
"today" can legitimately move backward relative to what's stored — an OS TZ
correction, an International Date Line crossing, or (for absorb_remote) two
machines simply disagreeing on the calendar date for hours at a time while
one stays home and one follows local time. A plain `!=` treated every one
of those as "a new day," silently wiping already-completed habits (inviting
a duplicate /did and a duplicate Neon point write) or dropping every
legitimate cross-machine completion for the disagreement window.

Fix: the gate is now forward-only (`today > stored_date`) — ISO YYYY-MM-DD
strings compare lexicographically the same as chronologically, so a stored
date that is equal to OR NEWER than the freshly-computed `today` is kept,
never reset.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


@pytest.fixture()
def mc(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mc_travel", _HERE / "mark-completed.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mc_travel"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "COMPLETED", tmp_path / "completed-today.json")
    monkeypatch.setattr(mod, "MIRROR_DIR", tmp_path / "z_ibx")
    return mod


def test_backward_date_move_does_not_wipe_completions(mc, tmp_path):
    """Stored date is 2026-08-24 (tomorrow), a TZ correction rolls the
    machine's computed 'today' back to 2026-08-23. The already-completed
    habit must survive, not be silently reset."""
    f = mc.COMPLETED
    f.write_text(json.dumps({
        "date": "2026-08-24", "names": ["hiit"], "points": {}, "ids": {},
    }))
    result = mc.append_names(["新闻"], today="2026-08-23", path=f)
    assert "hiit" in result["names"], "backward date move must not wipe existing completions"
    assert "新闻" in result["names"]
    assert result["date"] == "2026-08-24", "stored date must not regress either"


def test_forward_date_move_still_resets(mc):
    """The genuine new-day case must still reset — this is the behavior the
    forward-only gate is supposed to preserve."""
    f = mc.COMPLETED
    f.write_text(json.dumps({
        "date": "2026-08-22", "names": ["hiit"], "points": {}, "ids": {},
    }))
    result = mc.append_names(["新闻"], today="2026-08-23", path=f)
    assert result["names"] == ["新闻"]
    assert "hiit" not in result["names"]


def test_is_duplicate_today_survives_backward_date_move(mc):
    f = mc.COMPLETED
    f.write_text(json.dumps({
        "date": "2026-08-24", "names": ["talk with richard"], "points": {}, "ids": {},
    }))
    hit = mc.is_duplicate_today("talk with richard [20]", today="2026-08-23", path=f)
    assert hit == "talk with richard"


def test_is_duplicate_today_absent_when_stored_date_is_older(mc):
    f = mc.COMPLETED
    f.write_text(json.dumps({
        "date": "2026-08-22", "names": ["talk with richard"], "points": {}, "ids": {},
    }))
    hit = mc.is_duplicate_today("talk with richard [20]", today="2026-08-23", path=f)
    assert hit is None


def test_absorb_remote_merges_when_remote_date_is_ahead(mc):
    """Ix (stayed home) is already on 2026-08-24; Straylight (traveled west,
    following local time) still computes 2026-08-23 as its 'today'. The
    Ix completion must still merge in, not be dropped as stale."""
    mc.MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    (mc.MIRROR_DIR / "completed-today-ix.json").write_text(json.dumps({
        "date": "2026-08-24", "names": ["ix-habit"], "points": {}, "ids": {},
    }))
    n = mc.absorb_remote(today="2026-08-23")
    assert n == 1
    local = json.loads(mc.COMPLETED.read_text())
    assert "ix-habit" in local["names"]


def test_absorb_remote_skips_genuinely_older_remote(mc):
    mc.MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    (mc.MIRROR_DIR / "completed-today-ix.json").write_text(json.dumps({
        "date": "2026-08-22", "names": ["stale-habit"], "points": {}, "ids": {},
    }))
    n = mc.absorb_remote(today="2026-08-23")
    assert n == 0
