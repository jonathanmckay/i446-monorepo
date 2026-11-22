# tg tools

Quick Toggl tooling.

## `tg-fast.py`
Argument-shape interpreter behind the `/tg` skill. Resolves shortcodes, project
overrides (`@code`), time ranges, and backdated starts, then drives the
underlying `toggl_cli.py`.

## `janus.py`
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
  (account `m5c7`). 5-min file cache at `~/.cache/janus/gcal-YYYY-MM-DD.json`.
  Auto-refresh every 5 min; `r` forces a fresh fetch.
- **Outlook**: not yet wired.

### Launch
```sh
python3 ~/i446-monorepo/tools/tg/janus.py
```

Or, if you have `~/bin` on PATH, drop a wrapper:
```sh
echo '#!/usr/bin/env bash
exec python3 ~/i446-monorepo/tools/tg/janus.py "$@"' > ~/bin/janus
chmod +x ~/bin/janus
```

### Width
Targets `WIDTH_HINT = 50` columns. Adjust at the top of `janus.py` if your
right pane is a different size.

### Dependencies
Already present: `rich` (unused but available), `prompt_toolkit`,
`google-api-python-client`, `google-auth`, `wcwidth`. No new deps required.

## Neon tabs (2026-11-21)

janus also shows the Neon workbook's sheets, one per tab, with Excel's cell colours.

- `^T` next tab · `⌥1`–`⌥0` jump (`⌥1` is the timeline) · `Esc` back to the timeline
- On a Neon tab with the input box empty: arrows scroll, `PgUp`/`PgDn` page, `Home` returns to today's row, `End` goes to the bottom. The mouse wheel scrolls rows. The input box still takes /tg commands.
- Data comes from the workbook file on Ix, not from Excel: `neon_grid.py build` writes `~/.cache/janus/neon-grid.json` (text + resolved colours for every visible sheet) and janus re-runs it in a subprocess while a Neon tab is showing and the file has been re-saved. The view trails Excel by up to the daemon's save interval (~2 min) plus the 90 s rebuild floor.
- Today's row (this week's row on the weekly sheets) does not wait for that: every 5 s janus repaints it with the cell writes the Neon ledger journaled since the save (`ledger.writes_since`; status line `ledger write HH:MM`). 0分 keeps its own ledger source (`view.day_view`). Not covered until the next save and rebuild: formula cells that depend on a written cell, writes to other rows (backfills), and hand edits the daemon has not noticed. A snapshot built on another day is never overlaid; it is rebuilt first.
- Pinned header rows and frozen columns per sheet are declared in `neon_grid.SHEETS`.
- Colours are snapped to the terminal's 256 colours unless janus is started with `JANUS_TRUECOLOR=1` on a terminal chain that passes 24-bit colour (mosh ≥ 1.4, tmux with the `RGB` feature).
- `neon_tabs.py` holds the tab state and rendering (no prompt_toolkit); janus.py only routes keys.
