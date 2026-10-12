#!/usr/bin/env python3
"""Regression (bug 2026-10-12): dtd ⌥↵ on "validate no yt [25]" wrote +25 to
0分!U three times. Excel on Ix was slow; the append landed at 56s, did-fast's
60s watchdog then killed it (exit 124) BEFORE Step 7 recorded the id in
completed-today.json, so the 2026-08-31 claimed_task_ids guard
(test_did_fast_retry_no_double_credit) never saw it and dtd's retry queue
re-credited twice. Fix: every task-id 0分 append carries an idempotency key
the excel-http daemon dedupes on (services/excel-http idem_dup)."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("did_fast_idem", _HERE / "did-fast.py")
df = importlib.util.module_from_spec(_SPEC)
sys.modules["did_fast_idem"] = df
_SPEC.loader.exec_module(df)  # type: ignore[union-attr]


def test_idem_key_is_stable_per_task_day_col():
    r = SimpleNamespace(todoist_task={"id": "6hHP4FHjGX6FfJq7"})
    k = df._idem_key("0fen", r, "10/12", "U")
    assert k == "0fen:6hHP4FHjGX6FfJq7@10/12:U"
    assert df._idem_key("0fen", r, "10/12", "U") == k  # retry → same key
    assert df._idem_key("0fen", r, "10/12", "Q") != k


def test_name_only_items_get_no_key():
    assert df._idem_key("0fen", SimpleNamespace(todoist_task=None), "10/12", "U") is None


def test_batch_sends_idem_to_daemon(monkeypatch):
    sent = {}

    def fake_batch(sheet, appends, *, date=None, row=None, src=None):
        sent["appends"] = appends
        return {"ok": True, "row": 283, "results": [{"ok": True}]}

    monkeypatch.setattr(df.neon_excel, "batch_append", fake_batch)
    res = df.append_0fen_batch([("U", 25)], "10/12", ["validate no yt"], "0fen",
                               ["0fen:T1@10/12:U"])
    assert res.returncode == 0
    assert sent["appends"] == [{"col": "U", "value": "+25", "idem": "0fen:T1@10/12:U"}]
