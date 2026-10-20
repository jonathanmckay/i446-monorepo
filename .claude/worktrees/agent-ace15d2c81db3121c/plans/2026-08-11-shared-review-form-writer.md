# Feature: shared background writer for review-form TUIs (0s / 1s / xk887)

## Summary
Extract today's xk887 async-write fix (queue + single background worker
thread + recovery-dump-on-failure) into a shared module, and adopt it in
`0s.py` and `1s-survey.py` too — both currently write to Excel via the exact
same blocking `ix-osa.sh` pattern xk887 had, and **neither has any
recovery-on-failure mechanism at all** (a crash today loses the user's
typed answers with no trace).

## Design

### Approach
New module `lib/review_form_writer.py`, `BackgroundWriter` class:
- `queue(fn, *args, tag="", recovery_payload=None, **kwargs)` — non-blocking;
  deep-copies args/kwargs at queue time (a caller's `answers` dict keeps
  growing/mutating after queuing — xk887 already hit this); lazily starts
  one daemon worker thread on first use.
- One worker thread processes queued calls **one at a time** — writes to the
  same open Excel workbook must never overlap, whether from two form pages
  (xk887) or two different kinds of write (0s's Excel write + its did-fast
  mark-done call, both of which touch the same workbook).
- `drain(report=True) -> bool` — blocks until the queue is empty, prints a
  `→ tag ✓/✗` line per result, returns whether everything succeeded. Called
  once, after all interaction ends — never mid-flow (printing from a
  background thread while a prompt_toolkit full-screen Application owns the
  terminal corrupts the display).
- A failed call with `recovery_payload` set gets a JSON dump (mirrors
  xk887's `dump_recovery`); calls that aren't about durable user input (e.g.
  the did-fast/1s-mark-done subprocess) just report failure, no dump.
- Generic on purpose: `fn` is any callable, not hardcoded to a
  `write_answers(answers, date, sheets=...)` shape, since 0s/1s/xk887 each
  have a different signature.

**Per JM's call**: 0s/1s queue their Excel write AND their post-write
"mark done" call onto the *same* serialized queue (not concurrently) — the
two write to the same workbook via different paths (raw AppleScript vs.
did-fast's daemon-routed writes), so true parallelism there isn't proven
race-safe. This means 0s/1s gain durability (recovery-dump on failure,
which they have zero of today) and one shared, tested implementation —
**not** a wall-clock speedup, since there's no "next page" for them to
usefully advance into the way xk887 had. Worth being explicit about so
that's not read as a missed opportunity later.

### Files to change
- `lib/review_form_writer.py` — new, `BackgroundWriter`.
- `lib/test_review_form_writer.py` — new, canonical tests for the shared
  mechanics (serialization, snapshotting, recovery-dump, drain reporting).
  xk887/0s/1s's own tests then only need to verify correct *wiring*.
- `tools/xk887/xk887-survey.py` — replace the local `_write_queue` /
  `_writer_loop` / `queue_write` / `drain_writes` machinery with a thin
  wrapper around a module-level `BackgroundWriter`. Public function names
  (`queue_write`, `drain_writes`) and their call sites in `run_paginated`
  stay the same — existing tests should need no behavioral changes, just
  confirmation they still pass against the delegated implementation.
- `tools/0s/0s.py` — add `RECOVERY_DIR = ~/.cache/0s-recovery`; queue the
  Excel write (`recovery_payload=answers`) and the conditional `did-fast 0l`
  call, `drain()` once before the final print.
- `tools/1s/1s-survey.py` — same shape: `RECOVERY_DIR = ~/.cache/1s-recovery`,
  queue the Excel write + the `did/run.py 1s` mark-done call, `drain()`
  before the final print — and importantly *before* the `cmux close-surface`
  call (that tears down the pane; must not fire while a write might still
  need the terminal to report its result).
- `tools/0s/test_0s.py`, `tools/1s/test_1s_survey.py` — update the
  AST-inspection tests that assert the old blocking call shape; add
  recovery-dump-on-failure tests mirroring xk887's.

### Files to NOT change
- `lib/neon/excel.py` — the separate, already-shared daemon-routed Excel
  client used by `/did`, `/ate`, `/-1g`, etc. Not related to this raw
  `ix-osa.sh` AppleScript path; out of scope.
- No attempt at true concurrent Excel writes for 0s/1s (see Design above —
  explicitly decided against).

## Implementation steps
1. Write `lib/review_form_writer.py` (`BackgroundWriter`) + its own test file.
2. Refactor `xk887-survey.py` to delegate to it; re-run its existing 31 tests unchanged.
3. Wire `0s.py`: recovery dir, queue both calls, drain once; update/add tests.
4. Wire `1s-survey.py`: recovery dir, queue both calls, drain before cmux close; update/add tests.
5. Run the full suite across all three tools + the new shared module.

## Test plan
- [x] `BackgroundWriter.queue()` returns immediately even when `fn` blocks
- [x] Queued calls never run concurrently (one worker, serialized)
- [x] Args/kwargs are snapshotted at queue time, not read live later
- [x] A failed call with `recovery_payload` dumps JSON; one without doesn't
- [x] `drain()` returns `False` iff any queued call failed
- [x] xk887's existing 31 tests still pass against the delegated implementation
- [x] 0s: a failed Excel write now dumps recovery (regression test for the previously-nonexistent path)
- [x] 0s: Excel write + did-fast 0l call are both queued on the same writer, never concurrent
- [x] 1s: same two tests, 1s's own shape
- [x] 1s: `drain()` happens before `cmux close-surface` in source order

## Risks / open questions
- 0s/1s gain no wall-clock speedup from this (see Design) — durability +
  shared code only. Flagged so it isn't mistaken for a missed win later.
- `did-fast 0l`'s own `DIDFAST_WATCHDOG_SECS=110` env override (0s) must
  still be threaded through correctly when that subprocess call moves
  inside a queued closure.

## Result
- **Status:** Complete
- **Tests:** 7 new (`lib/test_review_form_writer.py`, canonical mechanics)
  + 2 new in xk887 (adjusted for delegation) + 2 new in 0s + 3 new in 1s.
  Combined suite (`lib/test_review_form_writer.py` +
  `tools/{xk887,0s,1s}/test_*.py`): **72/72 passing**.
- **Recovery dirs added:** `~/.cache/0s-recovery`, `~/.cache/1s-recovery`
  (xk887's `~/.cache/xk887-recovery` already existed).
- **Notes:**
  - A real bug surfaced and was fixed during implementation: naively
    constructing `_writer = BackgroundWriter(recovery_dir=RECOVERY_DIR)`
    once at module load time baked in a stale path — tests that
    `monkeypatch.setattr(m, "RECOVERY_DIR", tmp_path)` after load (the
    existing pattern in all three test suites) would have silently written
    real recovery dumps to the real `~/.cache/*-recovery` dirs during test
    runs instead of the tmp dir. Fixed by re-reading `RECOVERY_DIR` into
    `_writer.recovery_dir` at each `queue_write`/write-queue call site,
    across all three tools.
  - The heavily AST/source-inspection-based tests in `test_0s.py`
    (timeout/watchdog/env checks) needed NO changes — wrapping the existing
    `subprocess.run(...)` calls in nested closures (`_write_neon`,
    `_mark_0l`) before queuing them kept the literal call expressions
    reachable by `ast.walk()` on `main()`'s subtree, since nested
    `FunctionDef` bodies are included in that walk.
  - Implementation matched the plan with no other deviations.
