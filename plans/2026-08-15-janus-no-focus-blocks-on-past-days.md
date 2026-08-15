20
# Feature: No focus-block treatment on past-day views in janus

## Summary
When viewing a previous day in janus (day navigation, `STATE.day_offset != 0`), the last two 地支 blocks (亥 and 子) currently render via `render_focus_compact()` — the same wide, `FOCUS_ROWS`-detailed card style used for TODAY's *current/next* block — preceded by a "where now is" divider rule. On a past day this is meaningless (there is no "now" to mark) and visually singles out 亥 as if it were special, when it's just the last block of an already-fully-elapsed day. The user wants past-day views to render every block uniformly, with no focus-block treatment at all.

## Design

### Root cause
- `view_now()` (janus.py:1465) clamps a past day's "now" to 23:59:59, so `hour_to_block(23)` always resolves to 子 with no next block.
- `render_focus_compact()` (janus.py:3877) has an explicit 2026-07-27 fallback: "last block of the day (子)... render the fully-elapsed 亥 the same way as the current block" — this was written for TODAY's *live* view once the clock passes 22:00, but the same code path fires unconditionally for every past-day view too, since it only checks `cur`/`nxt`, never `STATE.day_offset`.
- `render_morning()` (janus.py:3323) renders every block *before* `detail_window()`'s start in the plain 3-row past-card style — but its loop's break condition (`blk_eh + 1 > cutoff.hour`) can structurally never include 子 (eh=23; would need cutoff.hour ≥ 24), so 子 has no other rendering path today. That's why 亥+子 fall through to `render_focus_compact()` unconditionally.
- `render_evening()` already self-gates correctly for past days (returns `[]` when `detail_window()`'s end lands on a different date) — no change needed there.

### Approach
Two small, additive changes, both gated on `STATE.day_offset != 0` so today's live rendering is provably untouched:

1. **`render_morning()`**: add a `whole_day = STATE.day_offset != 0` flag. When true, use `cutoff = view_now()` (23:59:59) instead of `detail_window()[0]`, and skip the loop's early-break entirely so every block in `BLOCKS`, including 子, renders through the same plain past-block pipeline (picks/spills/sleep/events/gaps/`_compact_block_lines(is_future=False)`) already used for 卯–戌.
2. **`render_all()`**: only emit the "now" divider rule + call `render_focus_compact()` when `STATE.day_offset == 0`. On a past day, `render_morning()` alone now covers the whole day, so nothing else is needed there — `render_evening()` already returns `[]`.

### Files to change
- `tools/tg/janus.py` — `render_morning()` (whole-day cutoff + loop bound), `render_all()` (gate the divider + `render_focus_compact()` call on `day_offset == 0`).

### Files to NOT change
- `render_focus_compact()` itself — its today-only fallback logic (2026-07-27 fix) stays exactly as-is; it simply won't be called on past days anymore.
- `render_evening()`, `view_now()`, `detail_window()` — already correct / unrelated.
- Day-navigation key bindings, `STATE.day_offset` mutation logic.

## Implementation steps
1. Refactor `render_morning()`'s cutoff/loop-bound to support a whole-day mode — `tools/tg/janus.py`.
2. Gate the divider rule + `render_focus_compact()` call in `render_all()` on `STATE.day_offset == 0` — `tools/tg/janus.py`.

## Test plan
- [ ] `render_morning(day_offset != 0)` includes a row/block for 亥 AND 子 (currently structurally impossible for 子).
- [ ] `render_morning(day_offset == 0)` output is byte-identical to current behavior at a few different times of day (regression guard — the whole-day branch must never fire for today).
- [ ] `render_all(day_offset != 0)` contains no focus-band divider rule and doesn't call `render_focus_compact()` (mock/monkeypatch and assert not called, or assert its distinctive output shape — e.g. no `FOCUS_ROWS`-wide card artifacts — is absent).
- [ ] `render_all(day_offset == 0)` still renders the focus band exactly as before (regression guard).
- [ ] 卯's sleep-collapse special case still functions in whole-day mode.

## Risks / open questions
- `_block_display_pts` / `_read_block_emojis` etc. should behave identically when called for 子 via the new path — no evidence they assume "never called for 子," but worth a quick sanity check during implementation.
- None of this touches event-cursor (`STATE.visible_events`) registration ordering in a way that should matter, since `render_morning`'s per-block calls already pass `track_selection=True` today.
