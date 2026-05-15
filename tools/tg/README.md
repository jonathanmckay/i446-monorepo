# tg tools

Quick Toggl tooling.

## `tg-fast.py`
Argument-shape interpreter behind the `/tg` skill. Resolves shortcodes, project
overrides (`@code`), time ranges, and backdated starts, then drives the
underlying `toggl_cli.py`.

## `tg-tui.py`
Narrow vertical TUI designed for the right pane of a split terminal (lives next
to `dtd`). Three jobs:

1. **Switch the running task** — press `c`, type any input that `/tg` accepts.
2. **Detail band** — ±2h around now in 15-minute slots. Past slots show Toggl
   entries, future slots show Google Calendar events, with a live `── now HH:MM ──`
   separator.
3. **Day overview** — collapsed morning view (Toggl, what happened) above the
   detail band; collapsed evening view (gcal, what's planned) below. Outlook is
   a placeholder section, to be wired later.

### Keys
| Key | Action |
|-----|--------|
| `c` | Enter command mode (input bar at bottom, then Enter) |
| `s` | Stop the running timer |
| `r` | Force refresh (Toggl + gcal cache busted) |
| `j` / `k` | Scroll the detail band ±30 min |
| `0` | Re-center detail band on now |
| `q` | Quit |
| `Esc` | Cancel command input |

### Data sources
- **Toggl**: imports `mcp.toggl_server.toggl_api` directly. Auto-refresh:
  current entry every 15 s, today's entries every 60 s.
- **Google Calendar**: `gcal_client.py` calls the API directly using the OAuth
  tokens at `~/.config/google-calendar-mcp/{tokens.json,gcp-oauth.keys.json}`
  (account `m5c7`). 5-min file cache at `~/.cache/tg-tui/gcal-YYYY-MM-DD.json`.
  Auto-refresh every 5 min; `r` forces a fresh fetch.
- **Outlook**: not yet wired.

### Launch
```sh
python3 ~/i446-monorepo/tools/tg/tg-tui.py
```

Or, if you have `~/bin` on PATH, drop a wrapper:
```sh
echo '#!/usr/bin/env bash
exec python3 ~/i446-monorepo/tools/tg/tg-tui.py "$@"' > ~/bin/tg-tui
chmod +x ~/bin/tg-tui
```

### Width
Targets `WIDTH_HINT = 50` columns. Adjust at the top of `tg-tui.py` if your
right pane is a different size.

### Dependencies
Already present: `rich` (unused but available), `prompt_toolkit`,
`google-api-python-client`, `google-auth`, `wcwidth`. No new deps required.
