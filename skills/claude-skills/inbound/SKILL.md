---
name: "inbound"
description: "Unified interrupt queue: rituals (-2n) and comms (ibx0). Replaces -2n as the top-level TUI. Usage: /inbound"
user-invocable: true
---

# Inbound (/inbound)

Unified interrupt queue that orchestrates inbound cards in one TUI.

**Current mode (rituals-only):** `/inbound` runs the -1n ritual cards only and
does **not** dive into the comms/email stream. It sets `INBOUND_SKIP_COMMS=1`
before handing to `-2n.py`; `/-2n` (which calls `main()` directly) still runs
the full flow including ibx0. Card sources:

1. **Ritual cards** — صلاة, time-gap audit, -1g goals (from -2n)
2. **Eat card** — "What did you eat during \<block\>?" → answer logged via `/ate`
3. ~~**Comms cards** — Gmail, iMessage, Slack, Outlook, Teams (from ibx0)~~ — skipped for now

To restore the comms dive, drop the `INBOUND_SKIP_COMMS` env set in
`tools/ibx/inbound.py` (or pass `skip_comms=False` to `-2n.main()`).

## Usage

```
/inbound          # auto-detect surface
/inbound --here   # force inline run in the current shell (escape hatch)
```

## Launch

Claude is the caller here, and its Bash tool has no TTY, so the TUI must land in
a terminal JM can see. Order:

1. **Shared launcher** (cmux tab → new window in this Claude's own tmux session,
   only if a client is attached, e.g. the `cc-*` sessions on Ix):
   ```bash
   ~/.claude/skills/_lib/open-form.sh inbound "bash ~/i446-monorepo/tools/ibx/inbound_wrapper.sh"
   ```
   `OPENED …` → confirm: `inbound opened (<cmux tab|tmux window>)`.
2. **Exit 10, and this is a local macOS session** (`[[ "$OSTYPE" == darwin* && -z "$SSH_CONNECTION" ]]`):
   open a Terminal.app tab.
   ```bash
   osascript -e 'tell application "Terminal" to do script "bash ~/i446-monorepo/tools/ibx/inbound_wrapper.sh"' \
             -e 'tell application "Terminal" to activate'
   ```
   Confirm: `inbound opened in a new Terminal.app tab`.
3. **Otherwise** say in one line that no visible terminal is reachable from this
   session and stop. Never run the wrapper inline from Claude's Bash tool (no TTY),
   and don't hand JM a command to type.

`--here` is for a human shell only: `bash ~/i446-monorepo/tools/ibx/inbound_wrapper.sh`
runs inline, and the wrapper handles Straylight delegation itself.

## Daily reset (today-only focus)

On the first `/inbound` (or `/-2n`) of a new day, `snapshot_build_order()`
archives yesterday's build order to `g245/v_logs/` and then resets the `## -1₲`
section: `clear_prayer_markers()` strips the emoji markers and
`clear_block_goals()` wipes every block's goals + `actual:` log back to an empty
`- [ ]` placeholder. So yesterday's goal cards never carry over — `/inbound`
starts focused on today. This runs **once per day** (gated by the v_logs
snapshot), so goals set later the same day are never re-wiped by a subsequent
run. Yesterday's full content stays recoverable in the v_logs snapshot.

## Card Ordering

Initial pass (on launch):
1. Ritual cards — صلاة, then **-1g** (the goal card leads the queue)
2. Eat card — "What did you eat during \<block\>?" → `/ate`

The previous-block **time-gap audit** ("你HHMM做了什么?") is **swallowed in
`/inbound`** (skip_comms) so it can't bury the current block's goal card. `/-2n`
still runs the gap audit. The gap-gathering is gated on `not skip_comms` in
`-2n.py`.

After cards: idle on the -1₲ goal panel until the 2h block changes (the wrapper
then restarts with a fresh ritual pass). The ibx0 comms stream is skipped in the
current rituals-only mode.

## Ritual Cards

Same as -2n Steps 1-3:

1. **صلاة** — check 0₦ `ص` column. If not done: `(y/skip)`
2. **-1g** — block-goal card. In `/inbound` it **always** fires: if the block has no goals, shows 3 block-aware suggestions synthesized from calendar (in-block meetings), open weekly 1g goals for the block's domain, open daily 0₲ items, and unfinished 0n habits (source-tagged `[cal]/[1g]/[0g]/[0n]`; pick 1,2,3 or type custom). If the block **already has goals**, it lists them and lets you **append** more (enter/skip keeps them as-is) — existing goals and their done-state are never overwritten (`append_block_goals`). In `/-2n` it stays silent when goals already exist (old behavior).
3. **Streak alerts** — after 4pm, habits with 7+ day streaks at risk

## Eat Card

After the ritual cards (rituals-only mode), `/inbound` asks **"What did you eat
during \<block\>?"**. For context it also shows the **last meal logged today** —
`last_meal_today()` reads the most-recent populated Earthly-Branch band from the
Neon `hcbi` row via ix and renders `last eaten: <food> · <band>` (e.g.
`last eaten: mocha + tacos · 午`). Best-effort: if ix is unreachable or nothing's
logged yet, the line is just omitted. The raw answer is passed straight through
to `/ate` via a detached `claude -p` subprocess (`spawn_ate_background`), which
logs it to the `hcbi` row. Skip with `skip`. Use the `/ate` input shape:
`food, kcal, protein (group n)`.

## Comms Cards (currently skipped)

When `INBOUND_SKIP_COMMS` is unset, delegates to `ibx0.main()` for the full
inbox flow (polling, cards, hotkeys). `/inbound` sets the flag, so this is
skipped for now; `/-2n` still runs it.

## Terminal Colors

- **blue** — idle, all cards processed
- **black** — processing (fetching, checking state)
- **red** — card ready for user action

## Design Notes

- `/inbound` replaces `-2n` as the top-level entry point
- `-2n` and `ibx0` remain independently callable
- `inbound.py` orchestrates; it imports from `-2n.py` and calls `ibx0.main()`
