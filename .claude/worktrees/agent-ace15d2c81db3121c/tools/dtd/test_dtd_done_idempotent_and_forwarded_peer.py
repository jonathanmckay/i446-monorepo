#!/usr/bin/env python3
"""Regressions (2026-10-19, native iOS/Android clients + tailscale serve):

1. /api/done must be idempotent by task id. A phone that locks mid-POST (iOS
   kills the request a few seconds after lock) or a tailnet blip makes the
   client retry; did-fast had already closed the task and credited Neon, so a
   second run would credit Neon twice. Once the id is in completed-today the
   handler answers {ok, already:true} without touching did-fast.

2. /api/where must see the phone's tailnet IP when the request came through
   `tailscale serve` (https://ix.tail9c51d5.ts.net:5560): the proxy connects
   from loopback and puts the peer in X-Forwarded-For. A header from a
   non-loopback peer is NOT trusted."""
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("dtd_idem", Path(__file__).parent / "dtd.py")
dtd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dtd)


def _capture(monkeypatch):
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout='{"results": []}', stderr="")
    monkeypatch.setattr(dtd.subprocess, "run", fake_run)
    return calls


def test_done_twice_runs_did_fast_once(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(dtd, "_completed_ids", lambda: {"ALREADY1"})
    client = dtd.app.test_client()
    r = client.post("/api/done", json={"id": "ALREADY1", "content": "i447 (15) [5]"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["already"] is True and body["closed"] is True
    assert body["points"] == 5
    assert calls == [], "did-fast must not run again for an id completed today"

    r = client.post("/api/done", json={"id": "FRESH2", "content": "i447 (15) [5]"})
    assert r.status_code == 200
    assert len(calls) == 1, "a fresh id still runs did-fast"


def _fake_where(monkeypatch):
    seen = []
    mod = types.ModuleType("where")
    mod.record_peer = lambda ip, tz: seen.append((ip, tz)) or "imago"
    monkeypatch.setitem(sys.modules, "where", mod)
    return seen


def test_where_uses_forwarded_for_only_from_loopback(monkeypatch):
    seen = _fake_where(monkeypatch)
    client = dtd.app.test_client()
    # through tailscale serve: loopback + X-Forwarded-For → the phone's IP
    client.post("/api/where", json={"tz": "America/Los_Angeles"},
                headers={"X-Forwarded-For": "100.72.38.25"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert seen[-1] == ("100.72.38.25", "America/Los_Angeles")
    # direct http from the phone: remote_addr as before
    client.post("/api/where", json={"tz": "Asia/Tokyo"}, environ_base={"REMOTE_ADDR": "100.68.22.5"})
    assert seen[-1][0] == "100.68.22.5"
    # a real peer sending a forged header is not believed
    client.post("/api/where", json={"tz": "Asia/Tokyo"}, headers={"X-Forwarded-For": "100.1.1.1"},
                environ_base={"REMOTE_ADDR": "100.68.22.5"})
    assert seen[-1][0] == "100.68.22.5"
