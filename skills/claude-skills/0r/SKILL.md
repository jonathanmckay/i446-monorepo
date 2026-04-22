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

Create events on the "jbm" calendar (m5c7 account) for each row in the table.
- Summary: `{What}` (prefix with project code if from Toggl: `[i9] vibing`)
- Description: source + attendees if any
- Skip gap rows
- Use `allowDuplicates: false` to avoid re-creating on reruns

## Idempotency

- Check if `~/vault/i156/daily/YYYY/MM-DD.md` already exists
- If it does, skip unless `--force` flag is passed
- This makes backfill safe to re-run

## Backfill Mode

`/0r --backfill START:END` iterates day by day from START to END.
- Skip days that already have files
- Rate limit: 1-second delay between days to avoid API throttling
- For Outlook backfill, check `~/vault/z_ibx/outlook-backfill/YYYY-MM-DD.json` first — use cached data if available instead of re-querying the API
- Report progress every 10 days

## Integration

- Can be called from `/0t` as a final step
- Can be scheduled as a nightly trigger on Ix

## Notes

- On Straylight, Outlook MCP calls go through the local Agency MCP (not Ix)
- Google Calendar uses account "m5c7"
- Toggl CLI works locally on both machines
- Create `~/vault/i156/daily/YYYY/` directory if it doesn't exist
