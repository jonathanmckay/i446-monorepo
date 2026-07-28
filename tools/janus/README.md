# janus mobile — swipeable timeline (iPhone-first)

Phone mirror of the `janus` timeline TUI (`tools/tg/janus.py`), styled like the
mobile dtd (`tools/dtd/dtd.py`): flat monospace list, Neon domain colors, 地支
block dividers. Shows today's Toggl entries and the untracked gaps between them.

- **URL (phone):** http://ix:5561  (same access path as dtd/dashboard)
- **Runs on:** Ix, LaunchAgent `com.jm.janus-mobile` (KeepAlive, RunAtLoad),
  logs `/tmp/janus-mobile.{log,err}`
- **Source:** `tools/janus/mobile.py` (single file, embedded HTML, Flask only)

## Swipe actions

- **Gap row → fill**: opens a dialog with start/end prefilled; saving creates
  the Toggl entry. `@code` in the description picks the project (like /tg).
- **Entry row → log 分**: shells the real `/did`
  (`did-fast.py "<desc> <minutes> [@project]"`), so points route exactly like
  the desktop pipeline: 0₦ habit → minutes into its 0n column; variable/1n+ →
  base + rate×minutes; Todoist word-overlap match → its `[N]`; otherwise the
  variable path writes minutes-as-points to the inferred domain + posthoc task.
  A per-day ledger (`~/.local/state/jm/janus-mobile-logged-<date>.json` on Ix)
  blocks double-logging; logged rows show ✓ and dim. Running entries refuse to
  log until stopped.

## Endpoints

- `GET /` — mobile UI
- `GET /api/timeline` — entries + gaps + 地支 dividers, tracked minutes, 0分 Σ
- `POST /api/fill {desc, start, end}` — create a Toggl entry for a gap
- `POST /api/log {id, desc, minutes, project}` — /did the entry's minutes

## Deploy / update

```bash
scp ~/i446-monorepo/tools/janus/mobile.py ix:~/i446-monorepo/tools/janus/mobile.py
ssh ix 'launchctl kickstart -k gui/$(id -u)/com.jm.janus-mobile'
```
