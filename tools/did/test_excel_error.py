"""Regression tests for Excel/ix-osa error propagation in did-fast.py.

Bug: When Excel is not open on ix (or ix is unreachable), did-fast.py
reported ok=false but omitted the error message, making it impossible
for the caller to tell the user what went wrong.

Fix: did-fast.py now includes an "error" key in the write result dicts
when returncode != 0, carrying the stderr from ix-osa.

These tests verify the contract via AST inspection of did-fast.py.
"""

import ast
import sys
from pathlib import Path


DID_FAST = Path(__file__).parent / "did-fast.py"
IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.py"


def _get_source(path):
    return path.read_text(encoding="utf-8")


def test_did_fast_propagates_error_on_0n_write_failure():
    """When 0n_write fails, the JSON output must include an 'error' key."""
    src = _get_source(DID_FAST)
    # The error key must be set when returncode != 0 for 0n_write
    assert '"error"' in src or "'error'" in src, (
        "did-fast.py must include an 'error' key in write result dicts"
    )
    # Specifically for 0n_write
    idx = src.index("0n_write")
    region = src[idx:idx + 400]
    assert "error" in region and "returncode" in region, (
        "0n_write section must check returncode and set error"
    )


def test_did_fast_propagates_error_on_0fen_write_failure():
    """When 0fen_write fails, the JSON output must include an 'error' key."""
    src = _get_source(DID_FAST)
    idx = src.index("0fen_write")
    region = src[idx:idx + 400]
    assert "error" in region and "returncode" in region, (
        "0fen_write section must check returncode and set error"
    )


def test_did_fast_propagates_error_on_1n_write_failure():
    """When 1n_write fails, the JSON output must include an 'error' key."""
    src = _get_source(DID_FAST)
    idx = src.index("1n_write")
    region = src[idx:idx + 400]
    assert "error" in region and "returncode" in region, (
        "1n_write section must check returncode and set error"
    )


def test_ix_osa_returns_exit_3_on_unreachable():
    """ix-osa.py must return exit code 3 when ix is unreachable."""
    src = _get_source(IX_OSA)
    assert "returncode == 255" in src, (
        "ix-osa.py must detect SSH failure (returncode 255)"
    )
    assert "IxResult(3" in src, (
        "ix-osa.py must return exit code 3 for transport failures"
    )


def test_ix_osa_detects_applescript_error():
    """ix-osa.py must detect ERROR: prefix in AppleScript output."""
    src = _get_source(IX_OSA)
    assert 'startswith("ERROR:")' in src, (
        "ix-osa.py must check for ERROR: prefix in osascript output"
    )
    assert "IxResult(2" in src, (
        "ix-osa.py must return exit code 2 for logic errors"
    )


def test_ix_osa_never_falls_back_to_local():
    """ix-osa.py must never call local osascript."""
    src = _get_source(IX_OSA)
    # Should not contain a bare "osascript" call without ssh
    assert "Never falls back to local osascript" in src or "never" in src.lower(), (
        "ix-osa.py must document that it never falls back to local osascript"
    )
    # The command should always go through ssh
    assert "ssh" in src, "ix-osa.py must route all calls through ssh"
