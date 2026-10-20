# Feature: -1g status panel on /inbound idle

## Summary
When `/inbound` reaches the inbox-zero idle screen (no email/comm card to show),
render the current 2h block's -1g goals in a centered panel. Done goals appear
struck-through; the panel shows a ☀️ marker when prayer for the block is done.

## Design

### Approach
- Reuse helpers already in `-2n.py`: `get_current_block`, `has_prayer_marker`,
  `BUILD_ORDER`, `BLOCKS`. Add a status-aware reader (`read_block_goals_with_status`)
  that returns `(text, done)` tuples — current `read_block_goals` drops `[x]` lines.
- Add `render_block_status_panel(block_name=None)` returning a Rich `Panel` ready
  to print. Centered alignment via `rich.align.Align.center`.
- `ibx0.py` imports these via `importlib.util` (since `-2n.py` has a leading dash,
  same trick `inbound.py` uses). Call right before the "Inbox zero — watching for
  new items..." line. Print once on entry; cheap and matches the existing
  one-shot status print there.

### Files to change
- `tools/ibx/-2n.py` — add `read_block_goals_with_status()` and
  `render_block_status_panel()`.
- `tools/ibx/ibx0.py` — import the helpers, render panel at inbox-zero entry.
- `tools/ibx/test_2n_blocks.py` — regression tests for new helpers.

### Files to NOT change
- Card flow inside `-2n.py` cards (Card 1/2/3 etc.)
- `inbound.py` orchestration
- Build-order data format (writes already use `- [ ]`; reads already accept
  both `[ ]` and `[x]`).

## Implementation steps
1. `-2n.py`: add `read_block_goals_with_status()` returning
   `{block: [(text, done), ...]}`.
2. `-2n.py`: add `render_block_status_panel(block_name=None)` → `Panel`. Strike
   done goals; prefix with ☀️ when prayer marker exists; show "(no goals set)"
   if empty.
3. `ibx0.py`: load `-2n.py` once via `importlib.util`; render panel before
   `Inbox zero — watching...`.
4. Tests: status reader, panel content (done goals struck, prayer marker
   surfaces, empty case), and AST check that ibx0 calls the renderer at idle.

## Test plan
- [ ] `read_block_goals_with_status` returns done flag for each goal
- [ ] `render_block_status_panel` strikes done goals
- [ ] Panel shows ☀️ when block has prayer marker
- [ ] Panel handles "no goals" gracefully
- [ ] AST: `ibx0.py` invokes the renderer near "Inbox zero" message
- [ ] Existing `test_2n_blocks.py` tests still pass

## Risks / open questions
- Panel won't refresh if user prays mid-idle. Acceptable for v1 — user can
  re-enter `/inbound`. Could add mtime-based refresh later.
- Centering may look awkward on narrow terminals — `Align.center` degrades fine.

## Result
- **Status:** Complete
- **Tests:** 7 new tests, 17/17 passing in `test_2n_blocks.py`. Pre-existing
  unrelated failure `test_final_fetch_includes_teams` in `test_ibx0.py` left as-is.
- **Notes:** `read_block_goals()` was refactored to delegate to the new
  status-aware reader (compat preserved by a regression test). Smoke-rendered
  against the live build order — panel displays correctly.
