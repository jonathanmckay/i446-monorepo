"""Prayer tap from the watch's hcmp complication (2026-11-22).

JM: "add the ability to increment if I press on the prayer complication".
The tap arrives as tag "ص:<epoch ms>" on /api/neg1n/complete, relayed by a
phone app that retries any failure without bound. So the rule under test is
AT MOST ONCE: one tap can never add two prayers, whatever fails.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import neg1n_status as ns  # noqa: E402

NOW = 1_795_370_000_000


def test_plan_leaves_ritual_tags_alone():
    assert ns.prayer_tap_plan("-1ibx", NOW, []) is None
    assert ns.prayer_tap_plan("سمش", NOW, []) is None


def test_plan_runs_a_fresh_tap_once():
    assert ns.prayer_tap_plan(f"ص:{NOW - 2000}", NOW, []) == "run"
    assert ns.prayer_tap_plan(f"ص:{NOW - 2000}", NOW, [str(NOW - 2000)]) == "dup"


def test_plan_drops_a_tap_retried_long_after():
    old = NOW - ns.PRAYER_TAP_TTL_MS - 1
    assert ns.prayer_tap_plan(f"ص:{old}", NOW, []) == "stale"
    assert ns.prayer_tap_plan("ص:", NOW, []) == "bad"
    assert ns.prayer_tap_plan("ص:12x", NOW, []) == "bad"


@pytest.fixture
def client(tmp_path, monkeypatch):
    runs = []

    class Proc:
        returncode = 0
        stdout = "ص: 9"
        stderr = ""

    def fake_run(cmd, **kw):
        runs.append(cmd)
        return Proc()

    monkeypatch.setattr(ns, "PRAYER_NONCES", tmp_path / "nonces.json")
    monkeypatch.setattr(ns.subprocess, "run", fake_run)
    monkeypatch.setattr(ns, "compute_status", lambda: {"block": "巳", "done": [], "not_done": []})
    monkeypatch.setattr(ns.time, "time", lambda: NOW / 1000)
    return ns.app.test_client(), runs, Proc


def _tap(c, nonce):
    return c.post("/api/neg1n/complete", json={"tag": f"ص:{nonce}"}).get_json()


def test_tap_increments_once_and_a_retry_does_not(client):
    c, runs, _ = client
    first = _tap(c, NOW - 1000)
    assert first["ok"] is True and first["prayer"] == "run" and first["applied"] is True
    assert runs == [["/usr/bin/python3", str(ns.SALAT_FAST)]]   # no args = +1
    assert "status" in first                                     # the phone parses this
    again = _tap(c, NOW - 1000)
    assert again["ok"] is True and again["prayer"] == "dup"
    assert len(runs) == 1


def test_failed_increment_is_not_retried(client):
    """Excel down: salat-fast exits 1 after QUEUEING the write. Answering
    ok:false would make the phone retry and queue another +1 each time."""
    c, runs, Proc = client
    Proc.returncode = 1
    r = _tap(c, NOW - 1000)
    assert r["ok"] is True and r["applied"] is False
    assert _tap(c, NOW - 1000)["prayer"] == "dup"
    assert len(runs) == 1


def test_claim_survives_a_restart_and_two_taps_both_count(client):
    c, runs, _ = client
    _tap(c, NOW - 3000)
    assert json.loads(ns.PRAYER_NONCES.read_text()) == [str(NOW - 3000)]   # persisted
    assert _tap(c, NOW - 1000)["prayer"] == "run"
    assert len(runs) == 2


def test_stale_tap_runs_nothing(client):
    c, runs, _ = client
    r = _tap(c, NOW - ns.PRAYER_TAP_TTL_MS - 5000)
    assert r["ok"] is True and r["prayer"] == "stale" and runs == []
