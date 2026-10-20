# Prof daemon → ix migration (staged, not active)

**Status:** scaffolding ready, not enabled. Flip when JM confirms.

## Why

Long-term goal: ix is the daemon center. Today, prof daemon runs on
straylight because Agency MCP only auths there (Microsoft-managed device).
Solution: keep MCP on straylight, expose a fixed-port HTTP endpoint, ssh-
forward it to ix.

## Architecture

```
┌──────────── straylight ────────────┐         ┌──────────── ix ────────────┐
│ launchd: com.mckay.agency-calendar │◄────────│ launchd: com.mckay.tunnel-  │
│   agency mcp calendar --port 7001  │  ssh -L │   agency-calendar           │
│   (auth lives here)                │  7001   │   ssh -N -L 7001:loc:7001   │
└────────────────────────────────────┘         │     straylight-refit        │
                                               │ launchd: prof-snapshot      │
                                               │   AGENCY_REMOTE_CALENDAR_   │
                                               │     PORT=7001               │
                                               └─────────────────────────────┘
```

## What's already in place

- `tools/ibx/agency_mcp.py` honors `AGENCY_REMOTE_<NAME>_PORT` and
  optional `AGENCY_REMOTE_HOST` env vars. If set, skips spawning a local
  MCP and connects to the named port instead.
- `tools/prof/*.py` work unchanged — they import `agency_mcp` and call
  `call_tool("calendar", …)`. The env vars do all the steering.
- ix ssh config already has `Host ix → ix.tail9c51d5.ts.net` and ix can
  ssh to `straylight-refit` (tested 2026-05-28).

## To activate (do not run until ready to flip)

### 1. Straylight: start fixed-port calendar MCP

```bash
# One-time test
~/.config/agency/CurrentVersion/agency mcp calendar --transport http --port 7001
# (verify with: curl localhost:7001/health or call_tool from a script)
```

Then install LaunchAgent `~/Library/LaunchAgents/com.mckay.agency-calendar.plist`
(template at `tools/prof/migration-scaffolding/com.mckay.agency-calendar.plist`).

### 2. ix: install ssh tunnel keepalive

```bash
scp tools/prof/migration-scaffolding/com.mckay.tunnel-agency-calendar.plist \
    ix:~/Library/LaunchAgents/
ssh ix 'launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mckay.tunnel-agency-calendar.plist'
```

### 3. ix: install prof daemon

```bash
# Code is already in sync (ix pulls main daily via auto-commit chain).
# Install LaunchAgent:
scp tools/prof/migration-scaffolding/com.mckay.prof-snapshot.plist \
    ix:~/Library/LaunchAgents/
ssh ix 'launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mckay.prof-snapshot.plist'
```

### 4. Straylight: disable old prof-snapshot

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.mckay.prof-snapshot.plist
mv ~/Library/LaunchAgents/com.mckay.prof-snapshot.plist{,.disabled-migrated-to-ix}
```

### 5. Verify

```bash
ssh ix 'AGENCY_REMOTE_CALENDAR_PORT=7001 python3 ~/i446-monorepo/tools/prof/prof_snapshot.py --today'
ls -lt ~/.config/prof/cal-*.json  # on ix
```

## Known gotchas

- **Auth refresh on straylight**: if Graph token expires, the calendar
  MCP on straylight will start failing. Today this surfaces as
  agency_mcp errors in ibx0 too — same recovery path (run any agency
  call interactively to trigger device-code prompt).
- **Tunnel restart on ssh reconnect**: launchd `KeepAlive=true` handles
  most cases. If ix loses tailscale, the tunnel will reconnect when
  network returns. ServerAliveInterval=30 in the wrapper script will
  drop dead connections within ~90s.
- **/d357 arrivals.jsonl path divergence**: arrivals live on whichever
  machine runs /d357 (straylight, where you work). prof_score on ix
  needs to read them via syncthing or a small sync hook. **For v1 keep
  prof_score on straylight** — only prof_snapshot needs to move.
- **Arrivals sync (deferred)**: when ready to move prof_score too, add
  `~/.config/prof/` to syncthing or rsync arrivals.jsonl into ix on a
  cron.

## Rollback

```bash
ssh ix 'launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.mckay.prof-snapshot.plist'
ssh ix 'launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.mckay.tunnel-agency-calendar.plist'
mv ~/Library/LaunchAgents/com.mckay.prof-snapshot.plist.disabled-migrated-to-ix \
   ~/Library/LaunchAgents/com.mckay.prof-snapshot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mckay.prof-snapshot.plist
```
