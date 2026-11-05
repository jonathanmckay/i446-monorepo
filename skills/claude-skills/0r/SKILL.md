---
name: "0r"
description: "Generate daily time record from Toggl + Outlook + Google Calendar. Writes vault markdown + Google Calendar archive. Usage: /0r [date] [--backfill YYYY-MM-DD:YYYY-MM-DD]"
user-invocable: true
---

# Daily Time Record (/0r)

Generate a canonical daily time record by merging Toggl, Outlook, and Google Calendar data.

## Usage

```
/0r              # yesterday (default)
/0r 4/21         # specific date
/0r --backfill 2026-01-01:2026-04-21   # date range
```

## Response Style

Minimal output:
```
0r → 2026-04-21: 14h 23m tracked, 47 entries → vault + gcal archive
```

## Sources

1. **Toggl** — `python3 ~/i446-monorepo/mcp/toggl_server/toggl_cli.py date YYYY-MM-DD`
2. **Outlook** — Agency MCP `calendar.ListCalendarEvents` (i9 work calendar)
3. **Google Calendar** — `mcp__google-calendar-mcp__list-events` account m5c7, all calendars

## Merge Logic

1. Pull all three sources for the target date
2. Sort all entries chronologically by start time
3. Dedup: if same time window + similar title appears in both Toggl and a calendar, keep the calendar version (richer metadata: attendees, links)
4. Calendar events are the skeleton (meetings you attended)
5. Toggl fills non-meeting time (deep work, habits, transitions)
6. Gaps >15 min between entries shown as "gap" rows
7. Map Toggl project codes to human-readable names

## Output 1: Vault Markdown

Write to `~/vault/i156/daily/YYYY/MM-DD.md`:

```markdown
---
date: YYYY-MM-DD
day: Monday
sources: [toggl, outlook, gcal]
total_tracked: 14h 23m
---

| Time | Duration | What | Source | Project | People |
|------|----------|------|--------|---------|--------|
| 07:30–08:15 | 45m | 0l | toggl | g245 | |
| 08:30–09:00 | 30m | SLT Prep | toggl | i9 | |
| 09:00–10:00 | 60m | SLT | outlook | i9 | Luke Hoban, Asha Sharma |
| 10:00–10:30 | 30m | gap | | | |
| 10:30–11:00 | 30m | Carolina 1:1 | outlook | i9 | Carolina Pinzon |
```

## Output 2: Google Calendar Archive

Create events on the **jbm** archive calendar — `calendarId: jonathan.b.mckay@gmail.com` — for each Toggl entry. This is the past-time archive; keep it off the m5x2 Cal (m5c7 primary, which is RE-only). The m5c7 account has owner access to the jbm calendar, so pass the calendarId explicitly.
- Summary: `{description} @{project}` (e.g. `soccer @xk87`)
- One event per Toggl entry, matching original start/end times
- Set `transparency: transparent` (don't block calendar as busy)
- Skip 睡觉 entries
- Use `create-events` bulk API with `sendUpdates: none`
- **Carry full Toggl metadata** in `extendedProperties.private` so the calendar is a complete record independent of Toggl: `toggl_id`, `project`, `project_id`, `tags` (comma-joined tag names), `dur` (seconds), `src: "0r"`. Tags and project here are the standalone-record signal — without them the archive loses the `-1/-2/-3` magnitude tags. (The historical backfill uses `src: "0r-backfill"` and the same keys; `~/bin/backfill_jbm_archive.py` is the reference.)

## Output 3: Build Order Actuals

Write Toggl entries into the build order (`~/vault/g245/5e-1/build-order.md`) under each 地支 time block in the `## -1₲` section.

### 地支 Time Block Mapping

JM's blocks are shifted one hour earlier than the classical 時辰 (classical 卯 = 05-07; here 卯 = 04-06, the 04:00 wake). Canonical source: `lib/blocks.py` `BLOCK_SCHEDULE`; `scripts/build-order-daemon.py` `BRANCH_HOURS` and `tools/tg/janus.py` `BLOCKS` agree. The build order's `## -1₲` section has one `- 卯` … `- 亥` header per block.

| 地支 | Hours |
|------|-------|
| 卯 | 04:00-06:00 |
| 辰 | 06:00-08:00 |
| 巳 | 08:00-10:00 |
| 午 | 10:00-12:00 |
| 未 | 12:00-14:00 |
| 申 | 14:00-16:00 |
| 酉 | 16:00-18:00 |
| 戌 | 18:00-20:00 |
| 亥 | 20:00-22:00 |

子 (22-24), 丑 (00-02), 寅 (02-04) are sleep blocks with no build-order header. Like `build-order-daemon.py`'s branch lookup, clamp: an entry starting before 04:00 goes under 卯, one starting at 22:00 or later under 亥.

### Routing Rule

Each Toggl entry is placed in the block where it **starts**. If an entry spans two blocks, it appears only once, in the starting block, with its full original time range.

Skip 睡觉 entries (sleep blocks have no header).

### Format

Under each 地支 line's existing items, append:
```
- 辰 😈
    - [ ] (existing goals)
    - actual:
        - 新闻 @hcmc (07:54-08:04, 10m)
        - morning tasks @g245 (08:04-08:10, 6m)
```

Only add `actual:` to blocks that have entries. Leave empty blocks unchanged.

### Duration Display

- Under 60m: `Nm` (e.g. `30m`)
- 60m+: `Xh Ym` (e.g. `1h10m`), drop minutes if zero (`1h`)

## Output 4: Build Order Archive

After writing actuals (Output 3), snapshot the complete build order to the daily archive:

1. Copy `~/vault/g245/5e-1/build-order.md` to `~/vault/g245/archive/2026/YYYY.MM.DD/build-order.md`
2. Create the date directory if it doesn't exist (`mkdir -p`)
3. This preserves the day's goals + actuals together before the live file gets reset for the next day

This runs after Output 3 so the archive includes the actuals.

## Idempotency

- Check if `~/vault/i156/daily/YYYY/MM-DD.md` already exists
- If it does, skip unless `--force` flag is passed
- This makes backfill safe to re-run
- **Build order actuals**: if any `actual:` blocks already exist in the `## -1₲` section, warn the user ("Actuals already present, clearing and rewriting") then remove all existing `actual:` sub-trees before writing new ones
- **Google Calendar**: use `allowDuplicates: false` on create-events to avoid duplicate events on rerun

## Backfill Mode

`/0r --backfill START:END` iterates day by day from START to END.
- Skip days that already have files
- Rate limit: 1-second delay between days to avoid API throttling
- For Outlook backfill, check `~/vault/z_ibx/outlook-backfill/YYYY-MM-DD.json` first — use cached data if available instead of re-querying the API
- Report progress every 10 days

## Integration

- Can be called from `/0t` as a final step
- Scheduled as a 4am daily trigger

## Notes

- **Runs nightly on Ix** via cron at 02:00 PT (`~/bin/run_0r_archive.sh` → `claude -p`). Ix has no Agency MCP, so skip the Outlook source there; Outlook is captured via the m5c7 "MSFT (Slow Sync)" calendar in the all-calendars read. On Straylight (manual runs), Outlook MCP calls go through the local Agency MCP.
- Google Calendar is the "m5c7"-authed account (its primary is the m5x2 Cal). The archive (Output 2) writes to **jbm = `jonathan.b.mckay@gmail.com`**, which the m5c7 account owns — pass that calendarId explicitly; never write the archive to primary.
- Toggl CLI works locally on both machines
- Create `~/vault/i156/daily/YYYY/` directory if it doesn't exist
- Build order is a single living document; actuals are written for the current day's `## -1₲` blocks
