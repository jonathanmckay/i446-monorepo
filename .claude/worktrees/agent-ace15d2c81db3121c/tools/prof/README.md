# prof — Professionalism daemon

Scores work meetings against the 7-rule professionalism system in
`vault/s897/professionalism-分.md`. Target: 90% of meetings score ≥ 0.

## Architecture (v1, runs on straylight)

```
/d357 start/stop ──► ~/.config/prof/arrivals.jsonl
                                                  │
~/.config/prof/cal-YYYY-MM-DD.json (5am snapshot) │
                                                  ▼
Agency MCP calendar ──► prof_score.py ──► stdout breakdown
```

v1 deliberately does **not** write to Neon — manual review only.
Migrate to ix once Agency MCP tunnel is built.

## Files

| File | What |
|---|---|
| `log_arrival.py` | Called by /d357 on start/stop. Appends to arrivals.jsonl. |
| `prof_snapshot.py` | Pulls today's Outlook calendar via Agency MCP, normalizes events, writes `~/.config/prof/cal-YYYY-MM-DD.json`. Runs daily at 5am via `com.mckay.prof-snapshot`. |
| `prof_score.py` | Scores all events for a day. Matches arrivals to events by name+time, applies R1/R2/R3a/R5/R6. Prints breakdown. |

## State

| Path | Purpose |
|---|---|
| `~/.config/prof/arrivals.jsonl` | One JSON record per /d357 lifecycle event |
| `~/.config/prof/cal-YYYY-MM-DD.json` | Calendar snapshot for that day |
| `~/.config/prof/snapshot.log` | LaunchAgent stdout/stderr |

## Trackable rules (auto)

| # | Rule | Signal |
|---|------|--------|
| 1 | No same-day reschedules | snapshot vs live calendar diff (event start moved) |
| 2 | Arrive ≤ 4 min | /d357 launch ts vs `event.start` |
| 3a | Preread for meetings > 5 attendees | `bodyPreview` length ≥ 600 chars |
| 5 | End on time (you run) | /d357 stop ts vs `event.end`, grace +5 min |
| 6 | Agenda (you organized) | `bodyPreview` length ≥ 400 chars |

Not tracked here (per spec): R3b (I read it — self-report), R4 (camera — weekly review).

## Daily test recipe

```bash
# 1. (One-time) Make sure today's snapshot exists. Tomorrow this is automatic at 5am.
python3 ~/i446-monorepo/tools/prof/prof_snapshot.py --today

# 2. Throughout the day, /d357 fires arrivals.jsonl entries automatically.

# 3. Review at any time:
python3 ~/i446-monorepo/tools/prof/prof_score.py

# 4. While rolling out, suppress the no-show -10 so you don't get crushed:
python3 ~/i446-monorepo/tools/prof/prof_score.py --no-no-show

# 5. JSON output for downstream tooling:
python3 ~/i446-monorepo/tools/prof/prof_score.py --json
```

## Known limitations

- **Body length is a weak proxy for "agenda exists."** Standard Teams join
  invites are ~253 chars in `bodyPreview`. Thresholds (400 for agenda,
  600 for preread) are tuned to require *some* text beyond the boilerplate,
  but a long Teams template could still false-positive. Iterate by pulling
  full `body.content` and stripping the Teams template.

- **R1 only works if a snapshot exists for the day before live scoring.**
  If you score a day without a snapshot, R1 is silently disabled.

- **Recurring 1:1s with "join via Teams" descriptions will fail R6.**
  This is correct by spec but may be aggressive for trusted recurring
  patterns. Future: whitelist subjects that opt out of R6.

- **No-show is a -10 by default.** While /d357 is becoming muscle memory,
  use `--no-no-show` to suppress that penalty.

- **Time zone handling assumes Windows tz names from Graph.** Mapped via
  `_WIN_TZ` in `prof_snapshot.py`. Add entries if you travel.

## To do (post-v1)

- [ ] Migrate to ix once Agency MCP tunnel exists
- [ ] Pre-meeting card for R3b (only when preread detected)
- [ ] Weekly batch card for R4 (camera-off retro-flag)
- [ ] Write daily success% to Neon
- [ ] Better agenda detection (strip Teams template from body)
- [ ] Treat new-on-the-day events as "added today" rather than untracked
