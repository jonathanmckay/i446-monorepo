"""Tests for lib/review_form_writer.py's BackgroundWriter -- the shared
non-blocking writer used by tools/{xk887,0s,1s}'s review-form TUIs.

Canonical coverage of the shared mechanics lives here; each tool's own test
suite only needs to verify correct wiring (that it queues the right calls
with the right recovery_payload, and drains before exiting/closing).
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_form_writer import BackgroundWriter  # noqa: E402


def test_queue_returns_immediately_even_if_fn_is_slow():
    w = BackgroundWriter()
    release = threading.Event()
    started = threading.Event()

    def _slow():
        started.set()
        release.wait(timeout=2)
        return "OK"

    t0 = time.monotonic()
    w.queue(_slow, tag="x")
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, "queue() must return immediately, not block on fn"

    assert started.wait(timeout=1), "the background worker should pick up the queued call"
    release.set()
    assert w.drain() is True


def test_queued_calls_never_run_concurrently():
    w = BackgroundWriter()
    lock = threading.Lock()
    in_flight = []
    max_concurrent = [0]

    def _tracked(n):
        with lock:
            in_flight.append(n)
            max_concurrent[0] = max(max_concurrent[0], len(in_flight))
        time.sleep(0.05)
        with lock:
            in_flight.pop()
        return "OK %d" % n

    for i in range(4):
        w.queue(_tracked, i, tag="call-%d" % i)
    assert w.drain() is True
    assert max_concurrent[0] == 1, "queued calls must be serialized, never concurrent"


def test_args_and_kwargs_are_snapshotted_at_queue_time():
    w = BackgroundWriter()
    captured = {}
    release = threading.Event()

    def _capture(answers):
        release.wait(timeout=2)
        captured.update(answers)
        return "OK"

    answers = {"good": "before"}
    w.queue(_capture, answers, tag="x")
    answers["good"] = "after-mutation"  # simulates a later step mutating the shared dict
    answers["extra"] = "should not appear"
    release.set()
    w.drain()

    assert captured == {"good": "before"}, \
        "queue() must snapshot args at queue time, not read a live-mutated dict later"


def test_recovery_dump_only_when_payload_given(tmp_path):
    w = BackgroundWriter(recovery_dir=tmp_path)

    def _boom():
        raise RuntimeError("workbook not open")

    # No recovery_payload -> failure reported, nothing dumped.
    w.queue(_boom, tag="no-payload")
    assert w.drain() is False
    assert not list(tmp_path.glob("*.json"))

    # With recovery_payload -> a JSON dump lands.
    w.queue(_boom, tag="xk88", recovery_payload={"xk88_good": "typed answer"})
    assert w.drain() is False
    dumps = list(tmp_path.glob("*xk88*.json"))
    assert dumps, "a failed call with a recovery_payload must dump JSON"
    import json
    assert json.loads(dumps[0].read_text()) == {"xk88_good": "typed answer"}


def test_recovery_payload_is_also_snapshotted_at_queue_time(tmp_path):
    """The recovery dump must reflect what was true AT QUEUE TIME, not
    whatever the caller's dict looks like by the time the write actually
    fails and dumps -- same reasoning as args/kwargs snapshotting."""
    w = BackgroundWriter(recovery_dir=tmp_path)
    release = threading.Event()

    def _boom(answers):
        release.wait(timeout=2)
        raise RuntimeError("still broken")

    payload = {"xk88_good": "original"}
    w.queue(_boom, payload, tag="xk88", recovery_payload=payload)
    payload["xk88_good"] = "mutated-after-queue"
    release.set()
    w.drain()

    import json
    dumped = json.loads(next(tmp_path.glob("*xk88*.json")).read_text())
    assert dumped == {"xk88_good": "original"}


def test_drain_returns_false_iff_any_call_failed():
    w = BackgroundWriter()
    w.queue(lambda: "ok", tag="a")
    w.queue(lambda: (_ for _ in ()).throw(RuntimeError("boom")), tag="b")
    assert w.drain() is False

    w2 = BackgroundWriter()
    w2.queue(lambda: "ok", tag="a")
    w2.queue(lambda: "ok too", tag="b")
    assert w2.drain() is True


def test_drain_is_a_noop_when_nothing_was_ever_queued():
    w = BackgroundWriter()
    assert w.drain() is True  # must not hang or raise with no worker started


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
