# Feature: -1n daemon — 2-hour mark + previous-block lock

## Summary
Replace the once-daily `+96` Neon write with a 2-hour daemon that, at each block
boundary from 04:00–22:00 local, sets the new block's marker cell to `1` (which
auto-bumps `Y` by 12 via the existing `=12*COUNT(...)` formula) AND locks the
just-ended block's points cell from rolling formula to literal value.

## Background

`0分` sheet structure (verified by reading row 112 / today):
- 9 marker columns (G, I, K, M, O, Q, S, U, W) — value `1` if block done
- 9 points columns (H, J, L, N, P, R, T, V, X) — locked literal once block ends, rolling `=D - SUM(prior)` until then
- `Y = 12*COUNT(G,I,K,M,O,Q,S,U,W)` — auto-totals 12 per marked block
- `X` is the trailing block (currently doubles as misc bucket)

Inferred time-block ↔ column mapping (8 hours of clock data confirms this):
| Block       | Marker | Points |
|-------------|--------|--------|
| 04–06       | G      | H      |
| 06–08       | I      | J      |
| 08–10       | K      | L      |
| 10–12       | M      | N      |
| 12–14       | O      | P      |
| 14–16       | Q      | R      |
| 16–18       | S      | T      |
| 18–20       | U      | V      |
| 20–22       | W      | X      |

## Design

### Approach
- New daemon mode `lock-and-mark` in `build-order-daemon.py`
- New launchd plist firing at minute `0` of even hours `4,6,8,10,12,14,16,18,20,22` (10 fires)
- At each fire, look up the (marker_col, prev_points_col) pair for the wall-clock hour
- AppleScript:
  1. Lock previous block: read `value of cell` (which Excel evaluates), write back as literal — but only if the cell currently contains a formula (`character 1 of formula = "="`). Idempotent.
  2. Set marker for new block to `1` only if currently empty. Idempotent.
- Remove `+96` write from `archive` mode (now redundant — Y accumulates throughout the day)
- Update rating email to read yesterday's `Y` value instead of hardcoded 96

### Fire schedule
| Time  | Marker set | Lock previous |
|-------|-----------|---------------|
| 04:00 | G         | (none)        |
| 06:00 | I         | H             |
| 08:00 | K         | J             |
| 10:00 | M         | L             |
| 12:00 | O         | N             |
| 14:00 | Q         | P             |
| 16:00 | S         | R             |
| 18:00 | U         | T             |
| 20:00 | W         | V             |
| 22:00 | (none)    | X             |

10 fires/day. 9 markers max → Y caps at 108 (consistent with existing formula's max).

### Files to change
- `i446-monorepo/scripts/build-order-daemon.py` — add `lock-and-mark` mode; remove `+96` Neon write from `archive`; rating email reads Y for yesterday
- `~/Library/LaunchAgents/com.jm.neon-lock-and-mark.plist` — NEW; `StartCalendarInterval` array of 10 entries (one per fire time)

### Files to NOT change
- `i446-monorepo/scripts/-1g-cron.py` — separate concern (build-order wipe)
- `~/Library/LaunchAgents/com.jm.1g-daily-reset.plist` — keeps wiping `-1₲` at 04:00
- `~/Library/LaunchAgents/com.jm.build-order-archive.plist` — same 03:59 schedule, just internal logic changes
- All other `0分` columns and other sheets

## Implementation steps
1. Add `BLOCK_FIRE_MAP` constant and `run_lock_and_mark()` function to `build-order-daemon.py`
2. Add `_neon_lock_cell()` and `_neon_set_marker()` AppleScript helpers
3. Wire `lock-and-mark` mode into `argparse`
4. Remove `write_neon_neg1(...)` call from `run_archive()`; replace with `read_neon_neg1()` to fetch yesterday's locked Y value
5. Update email summary to surface actual Y rating
6. Write `com.jm.neon-lock-and-mark.plist` with 10-entry `StartCalendarInterval`
7. Dry-run test on Straylight, then on ix
8. `launchctl bootstrap` on ix

## Test plan
- [ ] Dry-run at each of the 10 fire times (mock `dt.datetime.now()`) — confirm correct (marker, lock) pair returned
- [ ] Live run at 14:00 → marker Q=1, P locked from formula to value
- [ ] Idempotency: run twice in same hour → no double-write (marker check + formula-only lock)
- [ ] Edge: 04:00 fire → only sets G, no lock
- [ ] Edge: 22:00 fire → only locks X, no marker
- [ ] Edge: row not found for date → log error, no write
- [ ] Archive at 03:59 reports actual Y value (not hardcoded 96) in rating email

## Risks / open questions
- **Time-block mapping is inferred** from one user-confirmed datapoint (P=12-2pm) plus the alternating pattern. User should confirm before we deploy.
- **G/H rarely populated** in the data I sampled — is 04:00 fire too early to be meaningful? Could trim to 06:00–22:00 (9 fires, 8 marks) if user prefers.
- **22:00 fire locks X**, but `X` currently holds a "leftover/misc" formula `=D-SUM(...all prior)`. Locking it would freeze that residual. May need a dedicated "evening" treatment.
- **Mid-day load**: if the plist is loaded at, say, 15:00, the morning blocks won't be backfilled. Acceptable for v1.
- **Existing manual workflow**: if user already manually locks blocks throughout the day, this daemon would be a no-op (formula-only check) — but worth confirming there's no other automation already doing this.

## Result
- **Status:** Complete — loaded on ix, first live fire at 16:00 today
- **Files changed:**
  - `i446-monorepo/scripts/build-order-daemon.py` — new `lock-and-mark` mode, new AppleScript helpers (`neon_lock_cell`, `neon_set_marker`, `neon_read_y`); archive flow re-introduces `daily-reset` call + new `git_commit_archive` step; rating now reads actual Y instead of hardcoded 96
  - `~/Library/LaunchAgents/com.jm.neon-lock-and-mark.plist` — NEW, 10 fire times
- **Tests:** No pytest suite; verified via dry-runs at hours 4, 14, 22, 13 (off-hour no-op) on both Straylight and ix
- **Notes:**
  - Added scope creep mid-implementation: user requested `git commit` after archive completes "to prevent race conditions" with vault-autopush. Implemented `git_commit_archive()` — stages only `g245/archive` + the build-order md file, commits if there's a diff, doesn't push (vault-autopush handles that)
  - Re-added `daily-reset` call to archive flow so that write/defer/wipe land in one atomic commit. The existing `com.jm.1g-daily-reset` plist at 04:00 stays as a safety net (idempotent on already-empty section).
  - `NEON_DEFAULT_SCORE = 96` constant removed — Y now accumulates 12 per fire, totals naturally.
