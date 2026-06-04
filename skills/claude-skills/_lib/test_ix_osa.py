#!/usr/bin/env python3
"""Tests for ix-osa.py / ix-osa.sh — tg-tui write-notification.

Regression: neon (Excel) writes only refreshed tg-tui when they came from
/tg (tg-fast sends SIGUSR1 itself). Writes via ix-osa (did-fast, 0t-fast,
neon-write, ad-hoc heredocs) left tg-tui stale until its 120s ticker.
Fix: ix-osa signals tg-tui after every successful *write* (and never after
reads, which would self-loop via tg-tui's own fetch_points).
"""
import importlib.util
import os
import signal as _signal
import sys
import time
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent


def _load_ix_osa():
    spec = importlib.util.spec_from_file_location("ix_osa", _LIB / "ix-osa.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ix_osa"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def ix_osa():
    return _load_ix_osa()


@pytest.fixture()
def fake_tg_tui(monkeypatch, tmp_path):
    """Point the pidfile at our own pid and capture SIGUSR1."""
    received = []
    old = _signal.signal(_signal.SIGUSR1, lambda *_: received.append(True))
    cache = tmp_path / ".cache"
    cache.mkdir()
    (cache / "tg-tui.pid").write_text(str(os.getpid()))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    yield received
    _signal.signal(_signal.SIGUSR1, old)


def test_write_script_signals_tg_tui(ix_osa, fake_tg_tui):
    ix_osa._notify_tg_tui('set formula of theCell to "=0+5"')
    ix_osa._notify_tg_tui('set value of range "D5" of ws to 614')
    time.sleep(0.05)
    assert len(fake_tg_tui) == 2


def test_read_script_does_not_signal(ix_osa, fake_tg_tui):
    """Read-only scripts (tg-tui's own fetch_points) must NOT signal —
    signalling on reads would make tg-tui refresh itself in a loop."""
    ix_osa._notify_tg_tui('return value of range ("D" & todayRow) of ws')
    ix_osa._notify_tg_tui('return string value of range "C5" of ws')
    time.sleep(0.05)
    assert fake_tg_tui == []


def test_missing_pidfile_is_silent(ix_osa, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    ix_osa._notify_tg_tui('set value of range "D5" of ws to 1')  # must not raise


def test_stale_pid_is_silent(ix_osa, monkeypatch, tmp_path):
    cache = tmp_path / ".cache"
    cache.mkdir()
    (cache / "tg-tui.pid").write_text("999999")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    ix_osa._notify_tg_tui('set formula of theCell to "=1"')  # must not raise


def test_sh_helper_has_write_notify():
    """ix-osa.sh mirrors the same behavior: signals only on write verbs."""
    src = (_LIB / "ix-osa.sh").read_text()
    assert "tg-tui.pid" in src, "ix-osa.sh must notify tg-tui after writes"
    assert "kill -USR1" in src
    # Notification must be gated on write verbs, not unconditional.
    notify_idx = src.index("tg-tui.pid")
    gate = src[:notify_idx]
    assert '*"set value"*' in gate and '*"set formula"*' in gate, (
        "sh notify must be gated on set value/set formula write verbs"
    )
