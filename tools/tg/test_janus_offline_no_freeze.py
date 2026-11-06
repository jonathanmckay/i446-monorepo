"""Regression (2026-11-05 06:01, Straylight): "both dtd and janus are frozen
and looks like I'll have to manually kill the tab to restart."

janus-events.straylight-refit.tsv for that session: every Toggl read failed
with "[Errno 8] nodename nor servname" after ~30 s in getaddrinfo, the
current-timer errors land 60 s apart (30 s ticker sleep + 30 s stall), and the
session-start event came 60 s after launch. ticker_current, ticker_today and
main()'s startup reads ran on the asyncio loop, so janus took no keys and drew
nothing for ~30 s of every 60 s.

Fix: those reads go through _off_loop (a daemon thread the loop awaits);
fetch_today drops a result whose day changed while it was in flight.
"""
import asyncio
import datetime as dt
import importlib.util
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
SRC = (HERE / "janus.py").read_text()


def _load():
    spec = importlib.util.spec_from_file_location("janus_offline", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_offline"] = mod
    spec.loader.exec_module(mod)
    return mod


class _App:
    def invalidate(self):
        pass


def _max_loop_stall(mod, ticker, monkeypatch, stall=1.5):
    """Run `ticker` with its fetch stalled `stall` s (a DNS outage); return
    the longest gap a 50 ms heartbeat saw — i.e. the UI freeze."""
    monkeypatch.setattr(mod, "fetch_current", lambda *a, **k: time.sleep(stall))
    monkeypatch.setattr(mod, "fetch_today", lambda *a, **k: time.sleep(stall))
    monkeypatch.setattr(mod, "_assert_pid_file", lambda: None)
    real_sleep = asyncio.sleep

    async def short_sleep(d, *a, **k):
        await real_sleep(min(d, 0.05))

    monkeypatch.setattr(mod.asyncio, "sleep", short_sleep)

    async def go():
        gaps, last = [], time.monotonic()

        async def heartbeat():
            nonlocal last
            while True:
                await real_sleep(0.05)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        hb = asyncio.ensure_future(heartbeat())
        t = asyncio.ensure_future(ticker(_App()))
        await real_sleep(stall * 2.2)
        t.cancel()
        hb.cancel()
        return max(gaps)

    return asyncio.run(go())


def test_ticker_current_does_not_block_loop_when_dns_stalls(monkeypatch):
    mod = _load()
    assert _max_loop_stall(mod, mod.ticker_current, monkeypatch) < 0.5


def test_ticker_today_does_not_block_loop_when_dns_stalls(monkeypatch):
    mod = _load()
    assert _max_loop_stall(mod, mod.ticker_today, monkeypatch) < 0.5


def test_main_has_no_network_read_before_first_paint():
    body = re.search(r'^async def main\(\):\n(.*?)\nif __name__ == "__main__":', SRC, re.S | re.M).group(1)
    before_run = body.split("await app.run_async()", 1)[0]
    assert not re.search(r"^\s+fetch_(current|today)\(", before_run, re.M), (
        "main() must not run a Toggl read synchronously before the app paints")
    assert "_startup_toggl_fetches(app)" in before_run


def test_off_loop_propagates_errors_and_survives_closed_loop():
    mod = _load()

    async def boom():
        def f():
            raise ValueError("x")
        try:
            await mod._off_loop(f)
        except ValueError:
            return True
        return False

    assert asyncio.run(boom())

    async def abandon():
        mod._off_loop(time.sleep, 0.3)  # loop closes before the thread finishes

    asyncio.run(abandon())
    time.sleep(0.5)  # the thread's call_soon_threadsafe hits a closed loop: must not raise


def test_fetch_today_drops_result_when_viewed_day_changed_midflight(monkeypatch):
    mod = _load()
    mod.STATE.day_offset = 0
    sentinel = [{"desc": "kept"}]
    mod.STATE.entries = sentinel
    monkeypatch.setattr(mod, "_toggl_blocked", lambda *a: False)
    monkeypatch.setattr(mod, "_write_day_cache", lambda *a: None)

    def get_entries(**k):
        mod.STATE.day_offset = -1  # user pressed ctrl-← during the slow read
        now = dt.datetime.now(mod._tz())
        return [{"start": now.isoformat(), "stop": now.isoformat(), "description": "today"}]

    monkeypatch.setattr(mod.toggl_api, "get_entries", get_entries)
    mod.fetch_today(True)
    assert mod.STATE.entries is sentinel, "today's late result overwrote the newly viewed day"
