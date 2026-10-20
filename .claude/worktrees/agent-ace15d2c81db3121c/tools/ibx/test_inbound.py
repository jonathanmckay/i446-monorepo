"""Regression tests for the /inbound entry point.

Guards the class of bug where `inbound.py` went missing and the wrapper
silently treated python's exit-2 (file-not-found) as "user quit."
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
INBOUND_PY = HERE / "inbound.py"
WRAPPER = HERE / "inbound_wrapper.sh"


def test_inbound_py_exists():
    assert INBOUND_PY.exists(), f"missing {INBOUND_PY}"


def test_inbound_imports_and_exposes_main():
    spec = importlib.util.spec_from_file_location("inbound", INBOUND_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "main") and callable(module.main)


def test_wrapper_guards_missing_script():
    """Wrapper must fail loudly (non-empty stderr or non-zero exit) if SCRIPT
    is missing — not silently exit 2."""
    text = WRAPPER.read_text()
    assert "inbound.py" in text
    # Must reference an existence check pattern OR refuse to run silently.
    assert any(token in text for token in ("[[ ! -f", "[ ! -f", "test -f", "[[ -f")), (
        "wrapper should guard against missing SCRIPT to avoid silent exit-2"
    )
