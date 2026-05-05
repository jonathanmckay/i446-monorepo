# Feature: Background the -1g goal-setting subprocess in /inbound

## Summary
The `-1g` card in `/inbound` blocks the user for up to 120s waiting on a `claude
-p /-1g …` subprocess (which adds Todoist tasks). Move that subprocess to a
detached background process so the user proceeds straight to the next card
(meeting briefs / ibx0) immediately after typing goals.

## Design

### Approach
Today the card flow is:

```
parsed = parse_goals_text(resp)
run_1g(resp)                       # blocks ≤120s on subprocess.run
write_block_goals(block, parsed)   # local deterministic write
```

The local write was deliberately placed *after* the claude subprocess so a
silent claude failure couldn't clobber goals. With backgrounding, that
invariant changes: we commit the local write *first* (deterministic, sync,
fast), then fire-and-forget the claude subprocess for its only remaining
contribution — Todoist task creation.

New flow:

```
parsed = parse_goals_text(resp)
write_block_goals(block, parsed)   # immediate, sync, authoritative
spawn_1g_background(resp)          # subprocess.Popen, start_new_session=True
console.print("✓ -1g → 申 (claude syncing Todoist in background)")
# proceed to next card immediately
```

Key implementation details for `spawn_1g_background`:
- `subprocess.Popen([...], start_new_session=True, stdout=LOG, stderr=LOG,
  stdin=DEVNULL)` so the child survives parent exit and isn't killed when the
  TUI ends.
- Log to `~/.cache/inbound/1g-<ts>.log` (mkdir -p) so failures are debuggable.
- Same `--allowedTools` arg list as today's `run_1g`.
- No `wait()` — fire and forget. The local `write_block_goals` already
  guarantees the build order is correct.

### Why this is safe
The `/-1g` skill does two things:
1. Update the `## -1₲` section of the build order with the new goals.
2. Create matching Todoist tasks (with dedup).

Step 1 is exactly what `write_block_goals` does. Step 2 is the unique work the
subprocess must accomplish. By doing step 1 ourselves up front, the only
consequence of a slow/failed background claude is that Todoist tasks may be
delayed or missing — never that the local goals are lost.

There is a small race window where the backgrounded claude may also rewrite
the build order's `-1₲` section. Since both writes target the same content,
this is benign in practice (idempotent re-write of the same goal lines).

### Files to change
- `tools/ibx/-2n.py` — add `spawn_1g_background()`, swap call order in Card 2,
  update inline comments. Remove the now-dead "re-read goals to verify" branch
  since the local write is the source of truth.
- `tools/ibx/test_2n_blocks.py` — invert the order assertion in
  `test_run_1g_card_writes_locally_before_subprocess`; add new tests for
  `spawn_1g_background` (Popen-based, detached, logs to expected path).

### Files to NOT change
- `~/.claude/skills/-1g/SKILL.md` — skill behavior unchanged.
- `tools/ibx/inbound.py`, `inbound_wrapper.sh` — entry shim untouched.
- `tools/ibx/ibx0.py` — inbox flow unchanged.
- Other cards (salah, gaps, mtg, ibx0) — out of scope.

## Implementation steps
1. Add `spawn_1g_background(goals_text)` helper near `run_1g` in `-2n.py`.
2. Swap Card 2 logic: `parse_goals_text` → `write_block_goals` →
   `spawn_1g_background` → success message → continue.
3. Drop the now-irrelevant "re-read goals" verification block (replace with a
   one-line confirmation of `wrote_locally`).
4. Update `test_run_1g_card_writes_locally_before_subprocess` to assert the
   new order (write before spawn) and rename for clarity.
5. Add `test_spawn_1g_background_is_detached` — AST/text checks that the new
   helper uses `Popen` with `start_new_session=True` and does **not** call
   `wait()` / `communicate()`.
6. Add `test_card2_does_not_call_run_1g_blocking` — Card 2 region must not
   contain a blocking `run_1g(` call.

## Test plan
- [ ] Existing test suite (`pytest tools/ibx/test_2n_blocks.py`) still passes
      after updating the order assertion.
- [ ] New test: `spawn_1g_background` uses `Popen` with `start_new_session=True`.
- [ ] New test: Card 2 calls `write_block_goals` *before* `spawn_1g_background`.
- [ ] New test: Card 2 contains no synchronous `run_1g(` invocation.
- [ ] Manual smoke (out of scope for automated tests, noted for user): run
      `/inbound`, type goals, confirm card UI advances within ~1s.

## Risks / open questions
- **Orphan claude processes** if the user runs `/inbound` many times in quick
  succession. Acceptable: each run does dedup against existing Todoist tasks.
- **Background claude failures are silent.** Mitigated by writing logs to
  `~/.cache/inbound/`; user can inspect on demand.
- **Build-order race** between local write and backgrounded claude. Benign
  because both writes target the same goal text. If this proves problematic
  later, we can pass a `--no-build-order` flag to the skill (not in scope now).

## Result
- **Status:** Complete
- **Tests:** `tools/ibx/test_2n_blocks.py` 41 passed (was 39: +2 new, 1 modified).
  Broader ibx suite went from 120 → 122 passing; the 10 pre-existing failures
  in `test_ibx.py` / `test_ibx0.py` are unrelated and unchanged.
- **Notes:** No deviations from the plan. `run_1g` retained for backwards
  compatibility (existing tests for its `--allowedTools` still apply).
