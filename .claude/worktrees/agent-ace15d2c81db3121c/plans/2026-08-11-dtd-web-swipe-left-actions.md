# Feature: swipe-left action menu in dtd web (delay day / delay block / start)

## Summary
Replace dtd web's current swipe-left-to-start gesture (shipped earlier today)
with a swipe-left **reveal panel** exposing three buttons — delay to
tomorrow, delay to the next 2-hour block, and start a timer — mirroring
terminal dtd's `ctrl-d` (defer) / `ctrl-v` (block-snooze) / `enter` (start)
bindings.

## Design

### Approach
Terminal dtd has three distinct timing-manipulation bindings on a task:
- **`enter`** → `start_timer()` (already ported to dtd web this session, `/api/start`) — unchanged, reused as-is.
- **`ctrl-d`** → `defer-fast.py --id <id>` — with no extra args, defaults to
  **today+1 day**, 2 claimed points (confirmed in source). Recurring vs.
  non-recurring branching, dated-copy creation, etc. all live inside
  `defer-fast.py` already — the web action shells out to the exact same
  script rather than reimplementing any of that logic.
- **`ctrl-v`** → a **multi-block picker** (any remaining 地支 block today),
  writing `{id: start_hour}` into `~/.local/state/jm/dtd-block-snooze.json`
  (the file dtd web's `_snoozed_ids()` already reads — added this morning's
  bug fix). Since the request describes three flat buttons, not a
  picker-within-a-picker, **"delay (block)" is scoped to just the next
  block** (current block's end = next block's start) — same file/format,
  full round-trip compatibility with terminal dtd, just one predetermined
  target instead of a menu of them. If there's no next block left today
  (already in 亥, 20:00-21:59), it falls back to the day-delay behavior
  instead of silently doing nothing.

Frontend: swipe-right (complete) keeps its existing threshold-fly gesture,
untouched. Swipe-left changes shape entirely — from "drag far enough and it
fires" to a standard iOS-style **reveal panel**: dragging left slides the
row to expose three fixed action buttons underneath; release past a smaller
threshold and it snaps fully open (buttons stay visible until tapped or
dismissed); release before threshold and it snaps closed. Tapping a button
fires its action; tapping the row's visible content while open just closes
the panel (dismiss, no action). "Start" leaves the row in place after
completing, same as today (starting a timer doesn't finish the task);
"delay (day)" and "delay (block)" remove the row from the list, since the
task is now hidden until its delayed time — mirrors how "done" removes it.

### Files to change
- `tools/dtd/dtd.py` — `DEFER_FAST` path constant; `defer_task(task_id)`
  (shells out to `defer-fast.py --id`); `snooze_to_next_block(task_id)`
  (writes `dtd-block-snooze.json` directly, reusing `_snoozed_ids()`'s exact
  file format); `POST /api/delay-day`, `POST /api/delay-block` routes; the
  swipe-left reveal-panel markup/CSS/JS replacing the current fly-to-start
  gesture (keeps `/api/start` and its backend `start_timer()` untouched).

### Files to NOT change
- `tools/did/dtd.sh` — reference behavior only, not touched.
- `start_timer()` / `/api/start` — already correct from this morning's
  feature; only its frontend trigger (gesture → button tap) changes.
- No multi-block picker UI — explicitly descoped, see Design above.

## Implementation steps
1. Add `DEFER_FAST` constant; `defer_task(task_id: str) -> dict` (subprocess
   to `defer-fast.py --id <id>`, parse JSON stdout for `target_date`).
2. Add `snooze_to_next_block(task_id: str) -> dict` — read/write
   `dtd-block-snooze.json` (same shape `_snoozed_ids()` reads: `{date,
   snoozes: {id: hour}}`), computing the next block's start hour from the
   current wall-clock hour via the existing `block_hours` map; falls back to
   `defer_task` if there's no next block today.
3. Add `POST /api/delay-day`, `POST /api/delay-block` routes.
4. Rework the swipe-left frontend: reveal panel (3 buttons) replacing
   `trackStart`/`flyLeft`/`commitStart`'s fly-trigger; wire each button to
   its endpoint; row removal only for the two delay actions.
5. Tests for `defer_task`, `snooze_to_next_block` (including the no-next-block
   fallback and the exact JSON shape written), and the two new routes.

## Test plan
- [x] `defer_task` shells out to `defer-fast.py --id <id>` with no extra args (default +1 day)
- [x] `defer_task` surfaces a subprocess failure as `{ok: false, error}`, not a 500
- [x] `snooze_to_next_block` writes the correct next-hour for a given current hour, in the existing snooze-file format
- [x] `snooze_to_next_block` falls back to `defer_task` when already in the last block (亥)
- [x] `snooze_to_next_block` preserves any other ids already snoozed today (read-modify-write, not overwrite)
- [x] `snooze_to_next_block` discards a stale prior day's snoozes rather than carrying them forward
- [x] `POST /api/delay-day` / `POST /api/delay-block` reject missing `id`
- [x] Existing `/api/start` and its 6 tests remain unaffected

## Risks / open questions
- "Delay (block)" is next-block-only, not the terminal's full picker — flagged above as a deliberate scope call, not an oversight.
- No live browser verification available in this environment (same limitation as this morning's dtd web features).

## Result
- **Status:** Complete
- **Tests:** 9 new (`test_dtd_swipe_delay.py`); full `tools/dtd` suite 37/37 passing.
- **Live verification:**
  - `/api/delay-block` smoke-tested live against a throwaway fake task id
    (safe — only touches the local snooze cache file, no real Todoist
    mutation): current block was 未 (12:00-13:59), returned `hour: 14`
    (申) correctly. Cleaned up the throwaway entry afterward, restoring the
    snooze file to its prior (nonexistent) state.
  - `/api/delay-day` was **not** live-tested — `defer-fast.py` performs a
    real, non-trivially-reversible mutation on an actual Todoist task
    (reschedule, possible dated-copy creation, point deduction). Same
    reasoning as skipping a live test of `/api/start` this morning; relied
    on the unit tests instead (subprocess call-shape + failure surfacing).
  - Browser extension unavailable in this environment (as with the other
    dtd web features today) — no visual confirmation of the reveal-panel
    gesture itself; its logic directly extends the already-shipped and
    working swipe-right-to-complete gesture (same drag/snap/threshold
    mechanics, generalized to a variable open position).
- **Deployed:** restarted `com.jm.dtd` on ix (`launchctl kickstart -k`).
- **Notes:** implementation matched the plan with no deviations. Factored
  the block→hour map into a shared `BLOCK_HOURS` module constant (was
  previously inlined only in `build_tasks()`) so `snooze_to_next_block`
  could reuse it without duplicating the mapping.
