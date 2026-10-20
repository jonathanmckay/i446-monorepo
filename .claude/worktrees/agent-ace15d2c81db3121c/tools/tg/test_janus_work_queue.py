"""User request 2026-07-30: "janus is enforcing one command at a time ...
I want it to be able to enqueue tasks." The conversion_in_flight gate used
to REJECT a second ⌥↵/convert with "still converting the last one…"; now
jobs join a serial FIFO queue (_enqueue_work/_work_consumer). Still one at
a time — concurrent did-fast runs race ix-osa writes and can double-grant
points — but waiting replaces bouncing, and an identical queued/running
command is deduped (running it twice IS the double-grant)."""
import asyncio
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_queue", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_queue"] = mod
    spec.loader.exec_module(mod)
    return mod


class _App:
    def __init__(self):
        self.tasks = []

    def create_background_task(self, coro):
        t = asyncio.get_event_loop().create_task(coro)
        self.tasks.append(t)
        return t

    def invalidate(self):
        pass


def _reset(mod):
    mod.STATE.work_q = None
    mod.STATE.queued_cmds = set()
    mod.STATE.conversion_in_flight = False


def test_jobs_run_serially_in_fifo_order():
    mod = _load_tui()
    _reset(mod)
    order = []

    async def main():
        app = _App()
        gate = asyncio.Event()

        async def job_a():
            order.append("a-start")
            await gate.wait()
            order.append("a-end")

        async def job_b():
            order.append("b")

        assert mod._enqueue_work(app, "a", job_a, key="a")
        assert mod._enqueue_work(app, "b", job_b, key="b")
        await asyncio.sleep(0.05)
        assert order == ["a-start"], "b must wait for a — one job at a time"
        assert mod.STATE.conversion_in_flight, "worker busy while a job runs"
        gate.set()
        await mod.STATE.work_q.join()

    asyncio.run(main())
    assert order == ["a-start", "a-end", "b"]


def test_identical_command_is_deduped_while_queued():
    mod = _load_tui()
    _reset(mod)
    runs = []

    async def main():
        app = _App()
        gate = asyncio.Event()

        async def job():
            runs.append(1)
            await gate.wait()

        assert mod._enqueue_work(app, "did eat 0700-0730", job, key="eat")
        assert not mod._enqueue_work(app, "did eat 0700-0730", job, key="eat"), \
            "same key queued/running must be refused (double-grant guard)"
        gate.set()
        await mod.STATE.work_q.join()
        # once finished, the key frees up and can be enqueued again
        gate.clear()
        assert mod._enqueue_work(app, "did eat 0700-0730", job, key="eat")
        gate.set()
        await mod.STATE.work_q.join()

    asyncio.run(main())
    assert len(runs) == 2


def test_failed_job_does_not_kill_the_consumer():
    mod = _load_tui()
    _reset(mod)
    ran = []

    async def main():
        app = _App()

        async def bad():
            raise RuntimeError("boom")

        async def good():
            ran.append("good")

        mod._enqueue_work(app, "bad", bad, key="bad")
        mod._enqueue_work(app, "good", good, key="good")
        await mod.STATE.work_q.join()

    asyncio.run(main())
    assert ran == ["good"], "consumer must survive a failing job"


def test_flight_flag_clears_after_queue_drains():
    mod = _load_tui()
    _reset(mod)

    async def main():
        app = _App()

        async def job():
            pass

        mod._enqueue_work(app, "j", job, key="j")
        await mod.STATE.work_q.join()

    asyncio.run(main())
    assert not mod.STATE.conversion_in_flight
    assert mod.STATE.queued_cmds == set()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
