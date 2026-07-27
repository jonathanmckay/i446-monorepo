#!/usr/bin/env python3
"""Tests for janus mobile (tools/janus/mobile.py) — timeline building,
gap splitting at 地支 boundaries, and the double-log ledger guard."""
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_mobile", HERE / "mobile.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_mobile"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def jm(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
    return mod


def _entry(jm, sh, sm, eh, em, desc="work", code="i9", eid="1"):
    today = dt.datetime.now(jm.TZ).date()
    mk = lambda h, m: dt.datetime.combine(today, dt.time(h, m), jm.TZ)
    return {"id": eid, "desc": desc, "project": code,
            "color": jm.COLORS.get(code, jm.DEFAULT_COLOR),
            "start": mk(sh, sm), "end": mk(eh, em), "running": False}


def test_gap_splits_at_block_boundaries(jm, monkeypatch):
    """A long untracked stretch must be split into per-block gaps with the
    地支 dividers BETWEEN the pieces, not stacked above one huge gap."""
    e = _entry(jm, 7, 0, 7, 30)
    monkeypatch.setattr(jm, "_fetch_today", lambda: [e])
    monkeypatch.setattr(jm._dt, "datetime", jm._dt.datetime)  # no-op, keep real now

    now = dt.datetime.now(jm.TZ)
    if now.hour < 8:
        pytest.skip("needs the 巳 boundary to have passed for a stable assert")
    tl = jm.build_timeline()
    rows = tl["rows"]
    kinds = [(r["type"], r.get("label") or r.get("start")) for r in rows]
    # gap 00:00-04:00, divider 卯, gap 04:00-06:00, divider 辰, gap 06:00-07:00,
    # then the entry.
    assert kinds[0] == ("gap", "00:00")
    assert kinds[1] == ("divider", "卯 04:00")
    assert kinds[2] == ("gap", "04:00")
    assert kinds[3] == ("divider", "辰 06:00")
    assert kinds[4] == ("gap", "06:00")
    entry_idx = next(i for i, r in enumerate(rows) if r["type"] == "entry")
    assert rows[entry_idx]["minutes"] == 30
    assert rows[entry_idx - 1]["type"] in ("gap", "divider")


def test_short_gaps_hidden(jm, monkeypatch):
    e1 = _entry(jm, 6, 0, 6, 30, eid="1")
    e2 = _entry(jm, 6, 32, 7, 0, eid="2")  # 2-min gap < MIN_GAP_MIN
    monkeypatch.setattr(jm, "_fetch_today", lambda: [e1, e2])
    tl = jm.build_timeline()
    gaps = [r for r in tl["rows"] if r["type"] == "gap"
            and r["start"] == "06:30"]
    assert not gaps, "sub-threshold gaps must not render"


def test_ledger_blocks_double_log(jm, monkeypatch):
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        class P:
            returncode = 0
            stdout = json.dumps({"results": [{"step": "0n"}]})
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    r1 = jm.log_entry("e1", "新闻", 25, "hcmc")
    assert r1["ok"] and r1["step"] == "0n" and len(calls) == 1
    r2 = jm.log_entry("e1", "新闻", 25, "hcmc")
    assert r2.get("already") is True and len(calls) == 1, \
        "second swipe on the same entry must not re-run did-fast"


def test_log_entry_passes_minutes_and_project(jm, monkeypatch):
    seen = {}
    def fake_run(cmd, **kw):
        seen["text"] = cmd[-1]
        class P:
            returncode = 0
            stdout = json.dumps({"results": [{"step": "variable"}]})
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    jm.log_entry("e9", "leadership offsite", 83, "i9")
    assert seen["text"] == "leadership offsite 83 @i9"


def test_fill_gap_rejects_bad_times(jm):
    assert jm.fill_gap("x", "10:00", "09:00")["ok"] is False
    assert jm.fill_gap("x", "junk", "09:00")["ok"] is False
