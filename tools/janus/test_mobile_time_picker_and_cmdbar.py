#!/usr/bin/env python3
"""Janus web (2026-10-05):

1. Start/stop fields were text boxes with a number pad that kept the old
   HH:MM, so every edit meant deleting first. They are now native time wheels
   (type=time) that clear on tap and restore the old value if dismissed.
2. Command bar: the CLI's "type to run" line. Text goes to tg-fast.py
   verbatim, exactly as desktop janus's run_tg_fast does."""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
SRC = (HERE / "mobile.py").read_text()


def _load():
    spec = importlib.util.spec_from_file_location("janus_mobile_tp", HERE / "mobile.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_mobile_tp"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_time_fields_are_native_time_pickers():
    for fid in ("d-start", "d-end", "e-start", "e-end"):
        tag = re.search(rf'<input id="{fid}"[^>]*>', SRC).group(0)
        assert 'type="time"' in tag, tag
        assert "inputmode" not in tag, tag


def test_time_fields_clear_on_tap_and_restore_on_dismiss():
    assert "el.addEventListener('focus', ()=>{ el.dataset.prev = el.value; el.value = ''; });" in SRC
    assert "if(!el.value && el.dataset.prev) el.value = el.dataset.prev;" in SRC
    assert "['d-start','d-end','e-start','e-end'].forEach(id => timePicker(" in SRC


def test_add_dialog_no_longer_uses_now_sentinel():
    # a type=time input silently drops "now"
    assert "getElementById('d-start').value = 'now'" not in SRC


def test_command_bar_runs_tg_fast_verbatim(monkeypatch):
    jm = _load()
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="▶ coffee @epcn 14:00-14:15\n", stderr="")
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    r = jm.app.test_client().post("/api/run", json={"text": "coffee 1400-1415 @epcn"})
    d = r.get_json()
    assert d == {"ok": True, "msg": "▶ coffee @epcn 14:00-14:15"}
    assert calls[-1][-2:] == [str(jm.TG_FAST), "coffee 1400-1415 @epcn"]


def test_command_bar_reports_failure_and_rejects_empty(monkeypatch):
    jm = _load()
    monkeypatch.setattr(jm.subprocess, "run", lambda a, **k: subprocess.CompletedProcess(
        a, 1, stdout="", stderr="unknown project @zz\n"))
    d = jm.app.test_client().post("/api/run", json={"text": "x @zz"}).get_json()
    assert d == {"ok": False, "error": "unknown project @zz"}
    assert jm.app.test_client().post("/api/run", json={"text": "  "}).status_code == 400
