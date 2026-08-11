# Feature: swipe-left-to-start-timer in dtd web

## Summary
Add a second swipe direction to dtd web's task rows: swiping **left** starts
a Toggl timer for that task — the same action bound to `enter` in the
terminal `dtd` fzf TUI — with its own distinct (non-green) color so it reads
as a different action from the existing swipe-right-to-complete gesture.

## Design

### Approach
Terminal dtd's `enter` binding (`tools/did/dtd.sh:457-499`, the `DTD_START`
heredoc) does exactly this on the selected task:
1. Strip `(N)`/`[N]`/`{N}` annotations from the content.
2. Resolve a Toggl project: if the bare, 😈-stripped content matches a
   `RITUAL_DOMAIN` tag (`-1ibx`→i9, `-1g`/`-1l`→g245, `-1t`→n156, `سمش`→hcm —
   the SAME table dtd.py already carries for ritual card coloring), use that
   domain. Otherwise shell out to `tg-fast.py --resolve <name>` (the existing
   shortcode→project resolver already used by `/tg`).
3. Unconditionally stop whatever Toggl timer is currently running.
4. Start a new timer: `toggl_cli.py start "<name>" <project>`.

This needs only the task's raw content — no Todoist label lookup, no new
data threaded through from the frontend. dtd.py already has `raw` (and
`RITUAL_DOMAIN`, added for the ritual-coloring fix earlier today) available
per task, so the backend can replicate steps 1-2 exactly, then shell out to
the same `toggl_cli.py` binary terminal dtd already uses for steps 3-4 —
guaranteed identical behavior, not a re-implementation that could drift.

Unlike swipe-right (which completes and removes the row), starting a timer
doesn't finish the task — the row must spring back into place after the
swipe, not fly away. This mirrors terminal dtd, where pressing `enter`
starts a timer but leaves the task in the list.

Frontend: extend the existing single-direction (`Math.max(0, dx)`) swipe
handler to allow negative `dx` too. A second, independently-colored "track"
layer (`#trackStart`, revealed from the right edge, right-aligned "▶ start")
sits under the row alongside the existing green "✓ done" track (revealed
from the left edge). Only one track's opacity is driven at a time, based on
the sign of `dx`. Crossing the left threshold calls `/api/start` and shows a
toast; the row then snaps back (no removal, no tally change — starting a
timer doesn't earn points).

### Files to change
- `tools/dtd/dtd.py` — `start_timer()` helper, `POST /api/start` route,
  `#trackStart` markup/CSS (distinct blue, not `--go` green), bidirectional
  swipe JS (`move()`/`end()`/new `flyLeft()`/`commitStart()`).

### Files to NOT change
- `tools/did/dtd.sh` — the terminal binding is the reference behavior being
  mirrored, not something this touches.
- No persistent "this row is now the running timer" indicator after a
  successful start — out of scope; the user asked for the swipe gesture and
  its in-flight color feedback, not a standing running-state UI. Noted as a
  possible future enhancement, not built here.

## Implementation steps
1. Add `TOGGL_CLI`/`TG_FAST` path constants to `dtd.py`.
2. Add `start_timer(raw: str) -> dict` — strip annotations, resolve project
   (ritual table first, `tg-fast.py --resolve` fallback), stop then start via
   `toggl_cli.py`, return `{ok, clean, project}` or `{ok: False, error}`.
3. Add `POST /api/start` Flask route wiring `start_timer` to `{content}`.
4. Add `#trackStart` HTML + CSS (distinct blue accent, right-aligned "▶ start").
5. Rework `bindSwipe()`/`move()`/`end()` for bidirectional drag; add
   `flyLeft()`/`commitStart()` mirroring `fly()`/`commit()` but without row
   removal or tally changes.

## Test plan
- [ ] `start_timer()` on a ritual card's raw content (`"😈 -1g (15) [15]"`) resolves project `g245` via the ritual table, never calls tg-fast
- [ ] `start_timer()` on a plain task shells out to `tg-fast.py --resolve` with the annotation-stripped name
- [ ] `start_timer()` always stops the current timer before starting the new one (call order)
- [ ] `POST /api/start` with empty content → 400
- [ ] `POST /api/start` surfaces a subprocess failure as `{ok: false, error: ...}`, not a 500

## Risks / open questions
- `tg-fast.py --resolve` can return an empty project (unmapped shortcode) —
  matches terminal dtd's own behavior (starts with no project rather than
  guessing); not treated as an error.
- No offline handling beyond the existing toast-on-failure pattern already
  used by `/api/done` and `/api/add`.
