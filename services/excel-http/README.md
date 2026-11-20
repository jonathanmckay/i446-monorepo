# excel-http

Tiny localhost daemon on **ix** that fronts AppleScript writes to Neon. Cuts per-write latency from ~2-3s (cold-spawn `ssh ix osascript ...`) to ~200-400ms.

## Endpoints

```
GET  /health                                             → {ok, version}
POST /lookup    {sheet, date}                            → {ok, row}
POST /append    {sheet, col, date|row, value}            → {ok, row, col, value, formula}
POST /write     {sheet, col, date|row, value}            → {ok, row, col, value, formula}
POST /read      {sheet, col, date|row}                   → {ok, row, col, value, formula}
```

`value` for /append: `"+10"`, `"+'1n+'!S20"`, etc. (string concat onto existing formula).
`value` for /write: literal value, or `=formula`.

## Deploy on ix

The monorepo lives at `~/i446-monorepo` on ix already (Syncthing). Just copy the plist and load it:

```bash
ssh ix '
  cp ~/i446-monorepo/services/excel-http/com.mckay.excel-http.plist ~/Library/LaunchAgents/
  launchctl unload ~/Library/LaunchAgents/com.mckay.excel-http.plist 2>/dev/null
  launchctl load ~/Library/LaunchAgents/com.mckay.excel-http.plist
  sleep 1
  curl -s http://localhost:9876/health
'
```

Logs: `~/Library/Logs/excel-http.{log,err}` on ix.

## Smoke test from Straylight

```bash
ssh ix 'curl -s http://localhost:9876/health'
# → {"ok": true, "version": "1.0.0"}

ssh ix 'curl -s -X POST -H "Content-Type: application/json" \
  -d "{\"sheet\":\"0分\",\"date\":\"4/29\"}" \
  http://localhost:9876/lookup'
# → {"ok": true, "row": 117}
```

## Use from a skill

```python
from neon import excel
excel.append("0分", "R", date="4/29", value="+10")  # i9 col, +10 pts
excel.append("0n", "AE", date="4/29", value="+1")   # hiit col
excel.lookup_row("hcbi", "4/29")                    # → row int or None
```

The client tries the daemon first, falls back to direct `ssh ix osascript` if the daemon is down. Skills don't need to know the difference.

## Write queue (1.13.0 stage 1, 1.14.0 stage 2)

Off unless the daemon starts with `NEON_WRITE_QUEUE=1`; `queue_switch.sh on|off|status`
sets that in the installed plist and reloads. With it on:

- every `/append /write /batch /batch_write` is recorded in
  `~/.local/share/neon/write-intents.jsonl` before Excel is touched, applied at once when
  Excel answers, and otherwise answered `503/504 {"ok": false, "queued": true, "intent"}` and
  replayed in order later;
- clients send a request id (`rid`); `POST /intent {"rid"}` says what became of it
  (`pending | done | rejected | held | unknown`, with the handler's reply once finished) and
  never waits on Excel. A resend of the same rid is not a second write;
- `/read`, `/read_range` and `/reconcile` answer `503 queue_pending` while writes are still
  queued after a drain attempt (`"allow_stale": true` reads anyway);
- a queued write that ends held or rejected raises an alert and one dtd card a day
  (`queue_card.py`).

Callers must never rerun or fall back on a `queued` reply. Design and limits:
`vault/i447/i446/neon-off-excel-plan.md`, Step 4b.
