"""Session-wide isolation for the tools/did suite.

Set at import time (before any test module loads mark-completed.py, directly
or through did-fast.py) and inherited by every subprocess the tests launch.

JM_COMPLETED_MIRROR_DIR: mark-completed's vault mirror. Several tests
monkeypatch `mc.COMPLETED` to a fake completed-today.json but never touched
the mirror dir, so append_names / remove_names / absorb_remote pushed their
fake rows into the REAL ~/vault/z_ibx/completed-today-<host>.json; Syncthing
carried it to Ix, Ix absorbed it into its live completed-today.json, and it
synced straight back (2026-10-19: the bogus "-1g": "r2" ritual id).
"""
import os
import tempfile

_MIRROR = tempfile.mkdtemp(prefix="jm-test-completed-mirror-")
os.environ["JM_COMPLETED_MIRROR_DIR"] = _MIRROR
