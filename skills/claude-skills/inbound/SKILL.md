---
name: "inbound"
description: "Unified interrupt queue: rituals (-2n) and comms (ibx0). Replaces -2n as the top-level TUI. Usage: /inbound"
user-invocable: true
---

# Inbound (/inbound)

Unified interrupt queue that orchestrates all inbound cards in one TUI. Two card sources:

1. **Ritual cards** — صلاة, -1g goals, streak alerts (from -2n)
2. **Comms cards** — Gmail, iMessage, Slack, Outlook, Teams (from ibx0)

## Usage

```
/inbound          # auto-detect surface
/inbound --here   # force inline run in the current shell (escape hatch)
```

## Launch

Pick the first available path. **Do not skip detection** — invoking `cmux` blindly will fail on machines without it.

### Path A — cmux available (`command -v cmux` succeeds)

```bash
cmux new-surface --type terminal
# parse surface:N and pane:N from output, then:
cmux respawn-pane --surface surface:<N> --command "bash ~/i446-monorepo/tools/ibx/inbound_wrapper.sh"
cmux focus-pane --pane pane:<N>
```

Confirm: `inbound opened in a new cmux tab`

### Path B — macOS without cmux, local session (Straylight today)

Open a new Terminal.app tab via osascript:

```bash
osascript -e 'tell application "Terminal" to do script "bash ~/i446-monorepo/tools/ibx/inbound_wrapper.sh"' \
          -e 'tell application "Terminal" to activate'
```

Confirm: `inbound opened in a new Terminal.app tab`

### Path C — remote SSH / no GUI (phone, Termius, any SSH session)

Run inline in the current shell:

```bash
bash ~/i446-monorepo/tools/ibx/inbound_wrapper.sh
```

The wrapper handles Straylight delegation automatically. No special flags needed.

### Detection (one-liner)

```bash
if command -v cmux &>/dev/null; then
  # Path A — cmux flow
elif [[ "$OSTYPE" == "darwin"* ]] && [[ -z "$SSH_CONNECTION" ]]; then
  # Path B — Terminal.app
else
  # Path C — inline (phone SSH, Termius, remote session)
  bash ~/i446-monorepo/tools/ibx/inbound_wrapper.sh
fi
```

## Card Ordering

Initial pass (on launch):
1. Ritual cards — صلاة, -1g (instant, file reads)
2. Comms cards — ibx0 full flow (fetch, triage, card stream)

During idle/comms processing:
- **Streak alerts** — surface after 4pm during idle

## Ritual Cards

Same as -2n Steps 1-3:

1. **صلاة** — check 0₦ `ص` column. If not done: `(y/skip)`
2. **-1g** — check build order for current 2h block goals. If empty: show 3 block-aware suggestions synthesized from calendar (in-block meetings), open weekly 1g goals for the block's domain, open daily 0₲ items, and unfinished 0n habits. Source-tagged `[cal]/[1g]/[0g]/[0n]`. Multiline goal input; pick 1,2,3 from suggestions or type custom.
3. **Streak alerts** — after 4pm, habits with 7+ day streaks at risk

## Comms Cards

Delegates to `ibx0.main()` for the full inbox flow (polling, cards, hotkeys).

## Terminal Colors

- **blue** — idle, all cards processed
- **black** — processing (fetching, checking state)
- **red** — card ready for user action

## Design Notes

- `/inbound` replaces `-2n` as the top-level entry point
- `-2n` and `ibx0` remain independently callable
- `inbound.py` orchestrates; it imports from `-2n.py` and calls `ibx0.main()`
