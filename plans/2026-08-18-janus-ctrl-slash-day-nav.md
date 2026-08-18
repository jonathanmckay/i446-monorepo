# Feature: Free up [ and ] in janus for typing; add Ctrl+/ as a day-back chord

## Summary
Bare `[` and `]` currently double as day-navigation shortcuts in janus, which
means they can never be typed into a task/description field (e.g. `[10]`
point annotations). Remove those bare bindings and replace them with a
Ctrl-chord pair — `Ctrl+/` (back) and the already-existing `Ctrl+=` (forward)
— so navigation stays a single easy keystroke but can never collide with
typed content.

## Design

### Approach
- Drop the bare `"["`/`"]"` key bindings from `_day_back`/`_day_forward`
  entirely (`c-left`/`c-right` and the existing `f24`/`f23` CSI-u aliases for
  Ctrl+-/Ctrl+= stay as-is).
- Add a new CSI-u alias for Ctrl+/ following the exact pattern already
  established for Ctrl+-/Ctrl+= (`ANSI_SEQUENCES["\x1b[45;5u"] = Keys.F24`
  for Ctrl+-, `"\x1b[61;5u"` → F23 for Ctrl+=): `"\x1b[47;5u"` → a new spare
  key (`Keys.F25`), bound to `_day_back`. 47 is `ord("/")`, matching the
  `<codepoint>;5u` (Ctrl modifier) shape of the other two aliases.
- **Caveat (real, cannot fully resolve without live testing):** the Ctrl+-/
  Ctrl+= aliases were only discovered and pinned down by `cat -v` in the
  user's actual terminal (2026-07-24) — plain Ctrl+- didn't arrive as an
  ASCII control byte the way it "should" in every terminal. Ctrl+/ commonly
  arrives as raw `0x1F` (US, Ctrl strips bits 5-6 from `/` = 0x2F) in some
  terminals, or as the same CSI-u shape as Ctrl+-/Ctrl+= in cmux/Ghostty
  (which the existing comments confirm uses "fixterms" encoding). I'm
  implementing the CSI-u alias to match the established pattern, but this
  needs a live keypress check after deploy — if Ctrl+/ doesn't fire, the
  fix is almost certainly binding `"c-_"` (prompt_toolkit's name for the
  raw 0x1F byte) as an additional alias, the same class of fix Ctrl+-/
  Ctrl+= needed.
- This directly addresses "`]` for moving forward didn't work anyways" too,
  since `]` will simply no longer be bound to anything nav-related — no
  further need to root-cause it. (Most likely explanation, for the record:
  `_day_forward` no-ops with a "already on today" flash when
  `day_offset >= 0`, so pressing `]` while already on today's view — the
  default, most common state — does nothing by design, not by bug.)

### Files to change
- `tools/tg/janus.py` — remove `@kb.add("[")`/`@kb.add("]")`; add the new
  `Keys.F25`/`"\x1b[47;5u"` ANSI_SEQUENCES alias and `@kb.add("f25")` on
  `_day_back`; update the adjacent comments; update the footer hint (found
  via `grep -n "\[/\] day"`) from `[/] day` to whatever the new hint should
  read.
- `tools/tg/test_janus_day_nav_keys.py` — update
  `test_day_nav_alternatives_stay_bound` (drop `[`/`]` from the expected
  list, add `f25`), update or replace
  `test_footer_hint_names_the_bracket_keys` for the new hint text, add a new
  `test_csiu_ctrl_sequences_still_aliased`-style assertion for the Ctrl+/
  alias, and add an explicit regression test that bare `[`/`]` are NOT bound
  (mirroring the existing `test_bare_minus_and_equals_are_not_bound` guard)
  so this can't silently regress back to swallowing bracket characters.

### Files to NOT change
- `_day_back`/`_day_forward`'s actual navigation logic (offset math, caps,
  flash messages) — unchanged, only the key bindings driving them move.
- `c-left`/`c-right` and the existing `f23`/`f24` Ctrl+-/Ctrl+= aliases —
  left in place; Ctrl+= keeps working exactly as it does today for forward.

## Implementation steps
1. Edit `tools/tg/janus.py`: add the `Keys.F25` CSI-u alias next to the
   existing F23/F24 ones; remove `@kb.add("[")` from `_day_back` and
   `@kb.add("]")` from `_day_forward`; add `@kb.add("f25")` to `_day_back`;
   update the surrounding comments to describe the new scheme.
2. Update the footer hint string (wherever `[/] day` is rendered) to reflect
   the new keys.
3. Update `test_janus_day_nav_keys.py` for the new binding set (remove `[`/
   `]` expectations, add `f25`, add an explicit "bare `[`/`]` are not bound"
   guard, update the footer-hint test).
4. Run the full janus test suite.
5. Deploy `janus.py` to Ix if it's part of the deploy path used earlier this
   session (check — janus desktop TUI runs on Straylight only per this
   session's earlier findings, so this may be Straylight-only).

## Test plan
- [ ] `f25`/`Keys.F25` alias resolves `"\x1b[47;5u"` (mirrors
      `test_csiu_ctrl_sequences_still_aliased`)
- [ ] `_day_back` is bound to `c-left`, `f24`, and `f25`
- [ ] `_day_forward` is bound to `c-right` and `f23` only (no bracket, no f25)
- [ ] Bare `"["` and `"]"` have zero bindings (regression guard, same shape
      as the existing bare `-`/`=` guard)
- [ ] Footer hint no longer advertises `[/]`
- [ ] Existing `_day_back`/`_day_forward` offset/cap behavior tests still pass
      unmodified (logic didn't change)

## Risks / open questions
- Ctrl+/ may not arrive as the CSI-u sequence I'm assuming — needs a live
  keypress check in the user's actual terminal after deploy (see Approach).
  If it doesn't fire, next step is adding a `"c-_"` alias (raw 0x1F) too.
- Some terminals/OSes reserve Ctrl+/ for their own function (e.g. "toggle
  comment" in various editors) — unlikely to matter for a terminal app, but
  flagging since Ctrl+Arrow was intercepted by macOS Mission Control before.

## Result
- **Status:** Complete
- **Tests:** 1 new (`test_bare_brackets_are_not_bound`), 6 updated
  (`test_day_nav_alternatives_stay_bound`, `test_csiu_ctrl_sequences_still_aliased`,
  `test_footer_hint_names_the_ctrl_keys` renamed from
  `test_footer_hint_names_the_bracket_keys`, plus the module docstring).
  477/477 passing across the full janus test suite (up from 476).
- **Notes / deviations from the initial plan draft:**
  - `Keys.F25` doesn't exist in prompt_toolkit (F24 is the last spare
    function key) — used the otherwise-unused `Keys.F22` as the CSI-u alias
    target instead.
  - Bound BOTH `"f22"` (CSI-u alias, `\x1b[47;5u`) AND `"c-_"`
    (prompt_toolkit's native name for the standard raw 0x1F byte) to
    `_day_back`, since — unlike Ctrl+-/Ctrl+=, which have no standard
    control-byte encoding at all — Ctrl+/ does have one, and it's genuinely
    unknown which encoding this terminal will actually send. Whichever
    fires, `_day_back` runs.
  - Footer/docstring hints now read `^_/^= day` (caret notation for Ctrl+/,
    Ctrl+=) instead of `[/] day`.
  - **Still needs a live keypress check** — deployed to both Straylight and
    Ix, but the desktop `janus.py` process on Straylight is a foreground TUI
    in active use, so it wasn't restarted; its own staleness check will
    flag `⚠ RESTART — code updated` on next render, or restart manually to
    pick up the change and test Ctrl+/ for real.
