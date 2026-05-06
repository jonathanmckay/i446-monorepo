# Feature: Auto-bonus for long 0n sessions (冥想 / o314 → 长冥想 / long o314)

## Summary
For two special habits (`冥想`, `o314`), when a single `/did` logs ≥ 30
minutes, also award a 1neon bonus to the matching long-session habit
(`长冥想`, `long o314`) at `0.5 × minutes` points, cumulatively per week.
Today the user has to type the long entry by hand.

## Design

### Approach
Declarative — add a `bonus` block to the source habit's registry entry; the
runner inspects it after a successful 0n write and triggers a sibling 1n+
write.

```jsonc
// config/tasks.json
"冥想": {
  ...,
  "bonus": {
    "long_habit_id": "长冥想",
    "threshold_minutes": 30,
    "multiplier": 0.5
  }
}
"o314": {
  ...,
  "bonus": {
    "long_habit_id": "long-o314",
    "threshold_minutes": 30,
    "multiplier": 0.5
  }
}
```

`Habit` dataclass gains an optional `bonus: Optional[dict] = None` field;
`route.py` passes it through; `run.py:run_0n` checks and dispatches.

### Runtime flow
After the existing 0n write succeeds inside `run_0n`:

```python
bonus = d.get("bonus")
if bonus and minutes >= bonus["threshold_minutes"]:
    pts = int(minutes * bonus["multiplier"])
    apply_long_bonus(bonus["long_habit_id"], pts, target_date)
```

`apply_long_bonus(long_id, pts, target_date)`:
1. Resolve `long_habit = registry.get_habit(long_id)` (e.g. `长冥想`).
2. Look up `bonus_col = cols.col("1n+", long_habit.neon_header)`.
3. Compute `(mw, week_row) = _calc_mw(target_date)` using the same Sunday
   anchoring as standard 1n+ writes.
4. Read the current `1n+!{bonus_col}{week_row}` value, add `pts`, write back —
   cumulative across same-week sessions. (Same pattern as `cumulative_increment`
   in existing `run_1n`, but the increment comes from the runtime computation,
   not from registry config.)
5. Append literal `+{pts}` to `0分` at the long habit's domain `fen_col`
   (`思` for `hcm`). **Literal, not a `+'1n+'!cell` formula** — each session
   adds its own bonus exactly once and is not invalidated by later cumulative
   writes to the same 1n+ cell.
6. Print one extra line: `  ⤷ 长冥想 +26 → 1n+!AA{r} (52m × 0.5)`.

The bonus does NOT close any Todoist task (no recurring `1neon` task is
expected for these specifically); skip that step.

### `[N]` syntax for explicit minutes
The user invocation `/did 冥想 [52]` requires `[52]` to be parsed as
explicit minutes. Today `run.py:_parse_input` only accepts a bare trailing
`<digits>` token, while `[52]` is stripped from the routing query but never
read as a value. Extend `_parse_input` so a trailing `[N]` (after Toggl-time-
range and date stripping) also fills `explicit_minutes`. This is a strict
addition: existing `/did hiit [48]` calls (which currently auto-detect
minutes from Toggl) will start using 48 as the minute value, which is what
the user already expects per the brackets convention. (The 0分-domain-column
write semantic that `did-fast.py` uses for `[N]` is unrelated; `run.py` does
not implement that path, so no collision.)

### Files to change
- `config/tasks.json` — add `bonus` block to `冥想` and `o314`.
- `lib/registry.py` — add `bonus: Optional[dict] = None` field to `Habit`.
- `tools/did/route.py` — propagate `bonus` field into JSON output.
- `tools/did/run.py` — extend `_parse_input` to read `[N]`; add
  `apply_long_bonus()`; call it from `run_0n` post-write.

### Files to NOT change
- `tools/did/did-fast.py` — separate legacy runner used by `dtd.sh` and the
  ZeroNeonOverrideTests; out of scope for this change. (See "Open questions".)
- `~/.claude/skills/did/SKILL.md` — runner-level addition; the skill prose
  remains accurate.
- 1n+ sheet structure, week_row formula, 0分 layout — no changes.
- Other habits — only `冥想` and `o314` get a `bonus` block.

## Implementation steps
1. Add `bonus: Optional[dict] = None` to `Habit` dataclass (`lib/registry.py`).
2. Add `bonus` blocks under `冥想` and `o314` in `config/tasks.json`.
3. In `tools/did/route.py`, after building the `out` dict for a registry hit,
   include `out["bonus"] = h.bonus` when present.
4. In `tools/did/run.py`:
   - Extend `_parse_input`: accept trailing `[N]` token (strip and treat as
     `explicit_minutes`).
   - Add `apply_long_bonus(long_habit_id, pts, target_date)` helper.
   - Call it from `run_0n` after `_append_completed`, before the final print.
5. Tests (new file `tools/did/test_run_bonus.py` to keep run.py tests
   isolated from the legacy did-fast tests):
   - `test_bracket_n_parses_as_explicit_minutes`
   - `test_bonus_triggers_at_or_above_threshold`
   - `test_bonus_skipped_below_threshold`
   - `test_bonus_pts_equals_minutes_times_multiplier`
   - `test_non_special_habit_has_no_bonus`
   - `test_bonus_writes_cumulative_to_1nplus_and_literal_to_0fen`
   - These mock `excel.read`, `excel.write`, `excel.append`, `_calc_mw`,
     `todoist.*`, and `_fire_refresh` so no Excel/Todoist side effects.

## Test plan
- [ ] Existing test suites (`test_did_routing.py`, `test_excel_error.py`,
      `test_mark_completed.py`, `test_next_task.py`, `test_todoist_close.py`)
      still pass — none reference the new `bonus` semantics.
- [ ] New `test_run_bonus.py` covers happy path + threshold edge cases.
- [ ] Manual smoke (out of band): `/did 冥想 [52]` produces both a 0n write
      of 52 to `0n!AR` (today's row) and a +26 cumulative add to `1n+!AA`
      (this week's row), and `+26` appended to `0分!思`.

## Risks / open questions
- **did-fast.py drift.** The legacy fast path in `did-fast.py` does not
  invoke this bonus logic. If `dtd.sh` (the fzf picker) ever starts a 冥想
  or o314 entry, no bonus will fire. In practice `dtd.sh` is for picking
  open Todoist tasks rather than time-bound habit logs, but flagging.
- **Threshold semantics.** Spec says "over 30 minutes". I'm interpreting this
  as `>= 30` to be inclusive (the user's example used 52). Change to `> 30`
  if strict.
- **Multiplier truncation.** `int(minutes * 0.5)` floors odd-minute totals
  (e.g. 31m → 15 pts). Acceptable per the user's "26 for 52" example which
  is integer-clean. If half-points are desired, switch to `round` later.
- **Multiple sessions same day.** Each call adds independently — typing
  `/did 冥想 [40]` twice in one day will award 20 + 20 = 40 bonus pts.
  This matches "each session > 30 min counts as a longer session".

## Result
- **Status:** Complete
- **Tests:** 13 new (`tools/did/test_run_bonus.py`), 79/79 in did suite passing
- **Notes:** Threshold inclusive (>=30 triggers); bonus floors via `int(minutes * 0.5)`; 0分 gets literal `+pts` (not formula ref) so multiple sessions/day stack cleanly. `[N]` parsing in `run.py` is universal but only acts when the routed habit carries a `bonus` block — no collision with `did-fast.py`'s 0分-override semantics since `run.py` doesn't implement that path.
