# dtd — swipe-to-done task list (iPhone-first)

MVP Todoist replacement for the phone. A swipeable card list of **today | overdue**
tasks; **swipe a card right → runs the real `/did`** (`did-fast.py`): closes the
Todoist task *and* writes its `[N]`/`{N}` points to Neon.

- **URL (phone):** http://ix:5560  (same access path as the personal dashboard)
- **Runs on:** Ix, LaunchAgent `com.jm.dtd` (KeepAlive, RunAtLoad), logs `/tmp/dtd.{log,err}`
- **Source:** `~/i446-monorepo/tools/dtd/dtd.py` (single file, embedded HTML, no deps beyond Flask)

## Endpoints
- `GET /` — mobile UI
- `GET /api/tasks` — today+overdue from Todoist, points/domain parsed, future recurring dropped
- `POST /api/done {content}` — shells `did-fast.py "<content>"` (Todoist close + Neon write)

## Deploy / update
```bash
scp ~/i446-monorepo/tools/dtd/dtd.py ix:~/i446-monorepo/tools/dtd/dtd.py
ssh ix 'launchctl kickstart -k gui/$(id -u)/com.jm.dtd'
```

## Notes
- Points on completion follow the `/did` pipeline exactly (0₦ / 1n+ / Step-5 domain column).
- No undo in MVP; swipe threshold is 42% of card width to avoid accidents.
- Add to iPhone Home Screen (Share → Add to Home Screen) for a full-screen app shell.
