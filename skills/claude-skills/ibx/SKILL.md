---
name: "ibx"
description: "Process inbox: Gmail + iMessages + Slack in one unified queue. Opens a single interactive card TUI in a new cmux tab."
user-invocable: true
---

# Inbox (/ibx)

Launch the unified inbox processor — Gmail, iMessages, and Slack in one sorted queue.

## Usage

```
/ibx
```

## Steps

Open one new cmux surface:

```bash
cmux new-surface --type terminal
```

Parse the surface and pane refs from the output (e.g. `OK surface:6 pane:3 workspace:1`), then use the full ref tokens (e.g. `surface:6`, `pane:3`):

```bash
cmux respawn-pane --surface surface:<N> --command "bash ~/i446-monorepo/tools/ibx/ibx0_wrapper.sh"
cmux focus-pane --pane pane:<N>
```

Then confirm: `ibx opened in a new cmux tab — Gmail + iMessages + Slack in one queue.`

## Notes

- **iMessages** requires Terminal to have Full Disk Access (System Settings → Privacy & Security → Full Disk Access).
- **iMessages** tracks processed threads in `~/.config/imsg/processed.json`.
- **Slack** reads tokens from `~/.config/slack/tokens.json` — format: `{"m5x2": "xoxp-...", "github": "xoxp-..."}`. Skipped silently if file is missing.
- Items are ordered: Slack (newest first by ts) → Gmail → iMessages.
- Commands: `a` archive/done, `d` delete, `r <text>` reply, `s` skip, `t <text>` todo, `q` quit, or type anything for Claude.
