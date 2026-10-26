"""vault-autopush-alert: a single failed cycle must not raise a card
(2026-10-26: 07:28 push blip raised "vault-autopush stuck", 07:38 cleared)."""
import datetime as dt
import importlib.util
import json
import sys
import types
from pathlib import Path


def _load(tmp_path, monkeypatch):
    created, closed, mails = [], [], []
    todo = types.SimpleNamespace(create_task=lambda *a, **k: (created.append(a[0]) or {"id": f"t{len(created)}"}),
                                 close_task=lambda tid: closed.append(tid))
    svc = types.SimpleNamespace(users=lambda: types.SimpleNamespace(messages=lambda: types.SimpleNamespace(
        send=lambda **k: types.SimpleNamespace(execute=lambda: mails.append(1)))))
    monkeypatch.setitem(sys.modules, "todoist", todo)
    monkeypatch.setitem(sys.modules, "ibx", types.SimpleNamespace(get_gmail_service=lambda: svc))
    spec = importlib.util.spec_from_file_location("vaa", Path(__file__).with_name("vault-autopush-alert.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.STATE_PATH = tmp_path / "state"
    return m, created, closed


def test_single_failed_cycle_does_not_alert(tmp_path, monkeypatch):
    m, created, _ = _load(tmp_path, monkeypatch)
    m.fail("push failed")
    assert created == []
    m.clear()
    assert not m.STATE_PATH.exists()


def test_failing_past_grace_alerts_once_and_renag_replaces_card(tmp_path, monkeypatch):
    m, created, closed = _load(tmp_path, monkeypatch)
    m.fail("push failed")
    st = json.loads(m.STATE_PATH.read_text())
    st["first_failed_at"] = (dt.datetime.now() - dt.timedelta(minutes=31)).isoformat()
    m.STATE_PATH.write_text(json.dumps(st))
    m.fail("push failed")
    m.fail("push failed")            # within 24 h: no second card
    assert len(created) == 1
    st = json.loads(m.STATE_PATH.read_text())
    st["alerted_at"] = (dt.datetime.now() - dt.timedelta(hours=25)).isoformat()
    m.STATE_PATH.write_text(json.dumps(st))
    m.fail("push failed")            # re-nag closes the old card first
    assert len(created) == 2 and closed == ["t1"]
