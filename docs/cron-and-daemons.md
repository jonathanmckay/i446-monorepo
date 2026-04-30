---
title: "Cron Jobs & Daemons"
date: 2026-04-30
type: doc
tags: [i447, infrastructure]
source: manual
updated: 2026-04-30
---

# Cron Jobs & Daemons

Two machines run scheduled jobs. The split principle: ix (always-on server) runs everything except jobs that need Microsoft auth or local-only resources (iMessage DB, clipboard).

Crontabs are per-machine: `crontab -e` on Straylight edits Straylight's, `ssh ix crontab -e` edits ix's. They are independent; editing one does not affect the other.

## ix (server, always on, has Excel open)

### Cron Jobs

| Schedule | Script | Purpose | Log |
|---|---|---|---|
| */15m | `tools/ai-dashboard/periodic-sync.sh` | AI dashboard data sync | `.periodic-sync.log` |
| */30m | `scripts/copilot-ingest.py` | Ingest Copilot transcripts | `.copilot-ingest.log` |
| */30m | `tools/personal-dashboard/refresh-points-cache.sh` | Refresh dashboard points cache | `/tmp/refresh-points-cache.log` |
| :15 hourly | `scripts/export-claude-transcripts.py` | Export Claude Code transcripts | `ai-transcripts/claude-export.log` |
| 2h (06-22 even) | `scripts/build-order-enrich.py` | Populate build order with meetings + time entries | `/tmp/build-order-enrich.log` |
| 2h (even) | `scripts/0g-sync.py sync` | Bidirectional sync: build order 0g <> Todoist | `.0g-sync-stdout.log` |
| 04:00 | `scripts/-1g-cron.py daily-reset` | Wipe -1₲ section for new day | `.1g-cron.log` |
| 05:30 | `scripts/0g-sync.py cleanup` | Move unchecked 0g items to 以后的目标, clear Todoist | `.0g-cleanup-stdout.log` |
| odd hours | `scripts/-1g-cron.py block-end` | Remove #关键径路 from expired -1g tasks | `.1g-cron.log` |
| 22:00 | `tools/decision-capture/capture.py` | Scan AI logs for decisions | `.capture.log` |

### LaunchAgents

| Label | Schedule | Purpose |
|---|---|---|
| `com.jm.neon-lock-and-mark` | 2h (04-22 even) | Add +12 to -1₦ col P, mark block with ⏰ |
| `com.jm.build-order-archive` | daily | Archive yesterday's build order |
| `com.jm.build-order-link` | periodic | Link d357 meetings into build order |
| `com.jm.cc-bus` | event-driven | Claude Code event bus |
| `com.jm.claude-export` | periodic | Claude transcript export |
| `com.mckay.did-refresh-cache` | periodic | Refresh /did task cache |
| `com.mckay.excel-http` | always running | HTTP bridge to Excel on ix |

### Long-Running Processes (dashboards)

| Label | Port | Purpose |
|---|---|---|
| `com.jm.dashboard-personal` | :5558 | Personal dashboard |
| `com.jm.dashboard-ai` | :5555 | AI stats dashboard |
| `com.jm.dashboard-m5x2` | :5556 | AI m5x2 dashboard |
| `com.jm.dashboard-m5x2-goals` | :???? | m5x2 goals dashboard |

## Straylight (laptop, Microsoft auth, iMessage)

### Cron Jobs

| Schedule | Script | Purpose | Needs |
|---|---|---|---|
| */5m | `tools/mtg/mtg.py poll` | Calendar polling + pre-briefs | MS Graph |
| */30m | `tools/personal-dashboard/imsg_response_db.py` | iMessage response time DB | Local iMessage DB |
| */30m | `tools/ibx/sync_external_replies.py` | Sync external reply timestamps | Gmail/MS |
| hourly | `scripts/export-copilot-transcripts.py` | Export Copilot CLI transcripts | Local Copilot data |
| 2h | `tools/personal-dashboard/sync_outlook_responses.py` | Outlook response times | MS Graph |
| 6h | `tools/personal-dashboard/gen_email_stats.py` | Email volume stats | MS Graph / Gmail |
| 06:55 | `bin/prewarm-entraid.sh` | Pre-warm EntraID tokens | EntraID |

### LaunchAgents

| Label | Status | Purpose |
|---|---|---|
| `com.mckay.ibx-email-watcher` | running | Gmail IMAP push notifications |
| `com.mckay.ibx-imsg-watcher` | broken (exit 1) | iMessage new message watcher |
| `com.mckay.email-stats` | loaded | Email stats (may overlap with cron) |
| `com.mckay.transcript-rsync` | daily 03:00 | Rsync .claude/projects/ to vault |
| `com.mckay.screenshot-clipboard` | broken (exit 127) | Screenshot to clipboard |

### Disabled (migrated to ix 2026.04.30)

| Label | Was | Now on ix as |
|---|---|---|
| `com.mckay.0g-sync.plist.disabled` | LaunchAgent, 2h | cron: 0g-sync.py sync |
| `com.mckay.0g-cleanup.plist.disabled` | LaunchAgent, 05:30 | cron: 0g-sync.py cleanup |

## Known Issues

- `com.mckay.ibx-imsg-watcher` exits with code 1 on Straylight. Needs investigation.
- `com.mckay.screenshot-clipboard` exits with code 127 (command not found). Binary missing or wrong path.
- `com.mckay.email-stats` LaunchAgent may duplicate the `gen_email_stats.py` cron job. Check if both are needed.
