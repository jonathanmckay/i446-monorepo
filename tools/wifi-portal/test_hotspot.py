"""imago → fuchikoma switching: two slow checks switch, one doesn't, the
check is rate-limited, a failed join backs off, and nothing happens off
imago or when the SSID is redacted."""
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("hotspot", HERE / "hotspot.py")
hs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hs)


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "STATE", tmp_path / "hotspot.json")


def tick(t, mbps, ssid="imago", join_ok=True, joins=None):
    joins = [] if joins is None else joins
    return hs.run(now=t, ssid_fn=lambda: ssid, measure_fn=lambda: mbps,
                  join_fn=lambda s: joins.append(s) or join_ok)


def test_two_slow_checks_switch():
    joins = []
    assert tick(1000, 0.3, joins=joins) == "slow"
    assert tick(1000 + hs.CHECK_EVERY, 0.3, joins=joins) == "switched"
    assert joins == ["fuchikoma"]


def test_one_slow_then_fast_resets():
    assert tick(1000, 0.3) == "slow"
    assert tick(1000 + hs.CHECK_EVERY, 25.0) == "ok"
    assert tick(1000 + 2 * hs.CHECK_EVERY, 0.3) == "slow"


def test_checks_are_rate_limited():
    assert tick(1000, 0.3) == "slow"
    assert tick(1000 + 60, 0.3) == "waiting"


def test_failed_join_backs_off():
    tick(1000, 0.3)
    assert tick(1000 + hs.CHECK_EVERY, 0.3, join_ok=False) == "join_failed"
    assert tick(1000 + 2 * hs.CHECK_EVERY, 0.3) == "waiting"


def test_probe_error_is_not_a_strike():
    """A 403/DNS failure (measure → None) must not count toward switching."""
    joins = []
    assert tick(1000, None, joins=joins) == "no_reading"
    assert tick(1000 + hs.CHECK_EVERY, None, joins=joins) == "no_reading"
    assert joins == []


@pytest.mark.parametrize("ssid,want", [("fuchikoma", "not_on_imago"),
                                       ("Highgarden Fiber", "not_on_imago"),
                                       (None, "ssid_unknown")])
def test_only_acts_on_imago(ssid, want):
    joins = []
    assert tick(1000, 0.1, ssid=ssid, joins=joins) == want
    assert joins == []
