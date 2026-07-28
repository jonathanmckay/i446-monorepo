#!/usr/bin/env python3
"""Regression (2026-07-27): "did all the -1n for 午 but it's showing 7pts".

Ritual completions run on BOTH machines (Straylight dtd/skills, Ix mobile
dtd), and each stamped its own build-order.md — Syncthing last-writer-wins
then dropped whichever side synced second (午 lost 🎯 from Straylight and ⏱️
from Ix; the header merged to ☀️📧✅ and the recompute set the block term to
7 of 13). Stamps must have a single writer: Ix's copy, via ssh, with the
local write only as a noted fallback when Ix is unreachable — and a remote
"already stamped" must not double-credit points.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "did_fast_stamp", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_stamp"] = mod
    spec.loader.exec_module(mod)
    return mod


class _P:
    def __init__(self, out, rc=0):
        self.stdout = out
        self.stderr = ""
        self.returncode = rc


def test_stamp_on_ix_parses_ch_nc_and_failure(monkeypatch):
    df = _load()
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw.get("input", "")))
        return _P("CH\n")
    monkeypatch.setattr(df.subprocess, "run", fake_run)
    assert df._stamp_on_ix("午", "🎯") is True
    cmd, payload = calls[0]
    assert cmd[:2] == ["ssh", "-o"] and "ix" in cmd
    assert "stamp_emoji" in payload and "午" in payload and "🎯" in payload

    monkeypatch.setattr(df.subprocess, "run", lambda *a, **k: _P("NC\n"))
    assert df._stamp_on_ix("午", "🎯") is False

    monkeypatch.setattr(df.subprocess, "run", lambda *a, **k: _P("", rc=255))
    assert df._stamp_on_ix("午", "🎯") is None

    def boom(*a, **k):
        raise OSError("no ssh")
    monkeypatch.setattr(df.subprocess, "run", boom)
    assert df._stamp_on_ix("午", "🎯") is None


def test_run_ritual_routes_stamps_through_ix():
    src = (_HERE / "did-fast.py").read_text()
    body = src[src.index("def run_ritual"):]
    assert "_stamp_on_ix(block, emoji)" in body, \
        "off-Ix completions must stamp Ix's build-order copy (single writer)"
    assert "stamp_fallback_local" in body, \
        "Ix-unreachable fallback must be noted in the result"
    # Remote truth gates the credit: a remote NC must zero `changed` so the
    # P credit can't double-fire for a ritual another machine already stamped.
    assert "changed = remote" in body


def test_on_ix_hostname_detection(monkeypatch):
    df = _load()
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "Jonathans-Mac-mini.local")
    assert df._on_ix() is True
    monkeypatch.setattr(socket, "gethostname", lambda: "Straylight-Refit.local")
    assert df._on_ix() is False
