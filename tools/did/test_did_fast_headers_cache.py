"""Regression (2026-11-05): "一起饭 asked for points when this should be known by
the algorithm and time". A header refresh that read nothing from Excel (exit 0,
empty stdout) overwrote headers.json with no habits and stamped it fresh, so for
24 h every 0n/1n+ name looked unknown and janus asked for points."""
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent


@pytest.fixture
def df(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("df_headers", HERE / "did-fast.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["df_headers"] = m
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "HEADERS_PATH", tmp_path / "headers.json")
    return m


def _excel(df, monkeypatch, stdout):
    calls = []

    def run(script, timeout=0):
        calls.append(script)
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr(df, "ix_run", run)
    return calls


GOOD = "0N\n3\t0l\n1N\nX\t一起饭\n"


def _cache(df, n0, n1, refreshed):
    df.HEADERS_PATH.write_text(json.dumps({"refreshed": refreshed, "0n": n0, "1n": n1}))


def test_empty_read_keeps_the_last_good_headers(df, monkeypatch):
    _cache(df, {"0l": 3}, {"一起饭": "X"}, "2026-01-01T00:00:00-08:00")
    _excel(df, monkeypatch, "")
    assert "一起饭" in df.refresh_headers()["1n"]
    assert "一起饭" in json.loads(df.HEADERS_PATH.read_text())["1n"]


def test_section_only_read_counts_as_empty(df, monkeypatch):
    _cache(df, {"0l": 3}, {"一起饭": "X"}, "2026-01-01T00:00:00-08:00")
    _excel(df, monkeypatch, "0N\n1N\n")
    assert "0l" in df.refresh_headers()["0n"]


def test_empty_cache_marked_fresh_is_refreshed_once_it_is_old_enough(df, monkeypatch):
    # The file this bug left behind: fresh stamp, no habits.
    now = df._daytime.local_now().isoformat()
    _cache(df, {}, {}, now)
    calls = _excel(df, monkeypatch, GOOD)
    import os
    old = time.time() - 600
    os.utime(df.HEADERS_PATH, (old, old))
    assert "一起饭" in df.load_headers()["1n"]
    assert len(calls) == 1


def test_empty_cache_is_not_retried_on_every_call(df, monkeypatch):
    _cache(df, {}, {}, df._daytime.local_now().isoformat())
    calls = _excel(df, monkeypatch, "")
    df.load_headers()
    df.load_headers()
    assert calls == []
