"""Shared non-blocking writer for prompt_toolkit review forms (0s, 1s, xk887).

Each form's Excel write is a synchronous ix-osa.sh (AppleScript on host ix)
round trip that can take 15s+ when the daemon is under load. xk887 hit this
directly: it's a PAGINATED form, and a blocking write between pages meant the
next page couldn't render until the previous one's write finished
("the save function takes way too long, should be non-blocking while I go on
to the next field", 2026-08-11). 0s and 1s are single-page forms with no
"next page" to advance into, so adopting this doesn't buy them wall-clock
speed — but it does buy them something they never had: a recovery dump when
a write fails. Before this, an ix-osa hiccup meant an unhandled RuntimeError
straight out of main() and the user's typed answers were gone, no trace.

BackgroundWriter runs every queued call on a SINGLE worker thread, one at a
time, by design: two AppleScript writes racing against the same open
workbook is a real corruption risk, not just a style concern. That means
0s/1s queuing both their Excel write and their post-write "mark done" call
(did-fast, did/run.py) still runs them sequentially, same total wait as
before -- a deliberate choice (JM, 2026-08-11): those two write to the same
workbook via different paths (raw AppleScript vs. the daemon-routed writes
did-fast uses), so true concurrency between them isn't proven race-safe.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import queue
import threading
from pathlib import Path
from typing import Callable


class BackgroundWriter:
    """One instance per form process. Lazily starts its worker thread on the
    first queue() call, so a form that never writes anything never spawns a
    thread at all."""

    def __init__(self, recovery_dir: Path | None = None):
        self.recovery_dir = recovery_dir
        self._queue: "queue.Queue" = queue.Queue()
        self._results: list[tuple[str, bool, str, Path | None]] = []
        self._results_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            fn, args, kwargs, tag, recovery_payload = item
            try:
                result = fn(*args, **kwargs)
                with self._results_lock:
                    self._results.append((tag, True, result, None))
            except Exception as e:  # noqa: BLE001
                rec_path = (self._dump_recovery(recovery_payload, tag)
                           if recovery_payload is not None else None)
                with self._results_lock:
                    self._results.append((tag, False, str(e), rec_path))
            finally:
                self._queue.task_done()

    def _dump_recovery(self, payload, tag: str) -> Path | None:
        if self.recovery_dir is None:
            return None
        self.recovery_dir.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = "-%s" % tag if tag else ""
        path = self.recovery_dir / ("recovery%s-%s.json" % (suffix, ts))
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        return path

    def queue(self, fn: Callable, *args, tag: str = "", recovery_payload=None,
             **kwargs) -> None:
        """Non-blocking: hands `fn(*args, **kwargs)` to the background
        worker and returns immediately. `args`/`kwargs`/`recovery_payload`
        are deep-copied at queue time -- a caller's answers dict is
        typically one growing object mutated by later steps, so a live
        reference could be read by the worker after it's changed.
        `recovery_payload` is dumped as JSON only if `fn` raises; omit it
        for calls with no durable user input at stake (e.g. a mark-done
        subprocess whose failure just needs reporting, not a JSON dump)."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        snap_args = tuple(copy.deepcopy(a) for a in args)
        snap_kwargs = {k: copy.deepcopy(v) for k, v in kwargs.items()}
        snap_recovery = copy.deepcopy(recovery_payload) if recovery_payload is not None else None
        self._queue.put((fn, snap_args, snap_kwargs, tag, snap_recovery))

    def drain(self, report: bool = True) -> bool:
        """Block until every queued call has finished. Call exactly once,
        after all interaction ends -- never between pages/steps of a
        full-screen Application, since a background thread printing while
        one owns the terminal corrupts the display. Returns True iff every
        queued call succeeded."""
        if self._thread is None:
            return True
        self._queue.join()
        with self._results_lock:
            results, self._results[:] = list(self._results), []
        all_ok = True
        for tag, ok, msg, rec_path in results:
            if not ok:
                all_ok = False
            if not report:
                continue
            label = tag or "write"
            if ok:
                print("→ %s ✓ %s" % (label, msg), flush=True)
            else:
                print("→ %s ✗ FAILED: %s" % (label, msg), flush=True)
                if rec_path:
                    print("→ answers saved to %s -- replay once fixed" % rec_path, flush=True)
        return all_ok
