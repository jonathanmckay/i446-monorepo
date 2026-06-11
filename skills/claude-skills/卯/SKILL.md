---
name: "卯"
description: "Forced-linear wakeup sequence. Drives you through the five -1₦ block rituals (☀️ prayer, 🎯 -1g goal, ⏱️ time-log, ✓ task, 📧 inbox) with no skips — each must be completed to advance. The activating counterpart to /inbound. Usage: /卯"
user-invocable: true
---

# 卯 — Wakeup Sequence (/卯)

The whole purpose: get through **-1₦** (the block-ritual icons) the moment you
wake up. Where `/inbound` is a passive triage queue, `/卯` is an **activating**
engine — a **forced linear sequence** where each ritual icon must be completed
before the next card appears (commitment via friction).

Targets the **current** 2-hour block (intended for 卯, 04:00–05:59, but works
any time you wake).

## Card order

Activating order — get up → decide → commit → act → process:

1. **☀️ صلاة** — mandatory ack ("where is the sun?"). You can't advance until
   you're up. Writes the per-block prayer marker.
2. **🎯 -1g** — set the block intention. **≥1 goal required** (pick a suggestion
   number or type goals). Writes to the build order + syncs Todoist in the
   background.
3. **⏱️ time-log** — start a Toggl timer. **Required** — Enter accepts the top
   goal (→ g245) or type a description. Skipped automatically if a timer is
   already running. This is the commit-to-action keystone.
4. **✓ task** — do ONE thing now, then log it. Pick an unfinished `0n` habit or
   type a task; logged via `/did` in the background. **Required.**
5. **📧 inbox** — hands off to `ibx0` (the full inbox flow), which closes the
   -1₦ row for the block.

No card offers a skip; empty/invalid input re-prompts. **Ctrl+C** aborts the
whole sequence (escape hatch).

## Launch

Pick the first available path (same detection as `/inbound`).

### Path A — cmux available (`command -v cmux` succeeds)

```bash
cmux new-surface --type terminal
# parse surface:N and pane:N from output, then:
cmux respawn-pane --surface surface:<N> --command "bash ~/i446-monorepo/tools/ibx/wakeup_wrapper.sh"
cmux focus-pane --pane pane:<N>
```

Confirm: `卯 opened in a new cmux tab`

### Path B — macOS without cmux, local session

```bash
osascript -e 'tell application "Terminal" to do script "bash ~/i446-monorepo/tools/ibx/wakeup_wrapper.sh"' \
          -e 'tell application "Terminal" to activate'
```

Confirm: `卯 opened in a new Terminal.app tab`

### Path C — remote SSH / no GUI (Fuchikoma / phone / Termius)

Run inline in the current shell. The wrapper auto-delegates to Straylight when
on ix (for Excel `0n` reads + work email):

```bash
bash ~/i446-monorepo/tools/ibx/wakeup_wrapper.sh
```

### Detection (one-liner)

```bash
if command -v cmux &>/dev/null; then
  : # Path A — cmux flow
elif [[ "$OSTYPE" == "darwin"* ]] && [[ -z "$SSH_CONNECTION" ]]; then
  : # Path B — Terminal.app
else
  bash ~/i446-monorepo/tools/ibx/wakeup_wrapper.sh   # Path C — inline
fi
```

## Terminal colors

- **red** — a card is waiting for your action (the whole point: get up)
- **black** — processing (writing markers, starting timers)
- **blue** — sequence complete, handed off to inbox

## Design notes

- Built as a sibling to `/inbound`. `wakeup.py` loads `-2n.py` and reuses its
  tested primitives (`prompt_card`, prayer markers, `write_block_goals`,
  `spawn_1g_background`, `start_toggl`, `_unfinished_0n_today`, term colors)
  rather than duplicating them.
- One-shot: unlike `/inbound`, there is no idle loop — when the five icons are
  done it hands off to `ibx0` and exits.
- Already-complete icons (prayer logged, goals set, timer running) are detected
  and skipped, so a mid-block re-run only forces what's left.
- Pure response parsers (`parse_goal_response`, `resolve_task_response`,
  `resolve_timer_desc`) are unit-tested in `tools/ibx/test_wakeup.py`.
