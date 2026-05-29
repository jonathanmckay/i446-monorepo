# tg-tui: Purpose & Direction

**One-line:** The single pane that answers "what is my state right now?" so I never have to open Chrome, Excel, or any other status app.

## Core question

tg-tui should answer, at a glance: **am I on track right now?**

That breaks into:

1. **What am I doing?** Current running timer, visible at top AND bottom (top scrolls offscreen in a tall day).
2. **Am I caught up on tracking?** Time tracking gaps, 分 logging status for the current and prior blocks.
3. **Did I do the habits?** -1n habit completion status for today.
4. **What's next?** Next calendar event (Google Calendar + Outlook), next Todoist task.
5. **How did the last block go?** Top 4 items by time, block emojis going into build order, points achieved.

## Current state (v1)

- Live running timer at top
- Detail band (±2h around now): past = Toggl, future = Google Calendar, 15-min slots
- Collapsed morning (Toggl history) and evening (gcal upcoming) sections
- Outlook placeholder (not wired)
- Task switching via `c` key (delegates to tg-fast.py)

## Roadmap

### P1: Fix what's broken

1. **Remove the Outlook placeholder line.** It's visual noise until wired.
2. **Wire Outlook events.** Use the Microsoft Graph API (or Outlook MCP if available) to pull today's events from jomckay@microsoft.com. Merge into the detail band and evening section alongside Google Calendar events. Color-code by source (gcal vs outlook) or by project mapping.

### P2: Block summaries

3. **Completed block summary.** For each finished Earthly Branch block, show:
   - The 4 most important entries (weighted by duration), displayed chronologically
   - The emoji string for that block (the same emojis that go into build order / dtd)
   - Points achieved in that block (read from Neon 0分 tab)

### P3: Duplicate current timer at bottom

4. **Current timer at bottom.** Mirror the `▶ description · code  elapsed` line at the bottom of the TUI, just above the hint bar. The top one scrolls out of view on long days; the bottom one is always visible.

### P4: Status indicators

5. **Neon points for current block.** Read from Neon 0分 and show points logged vs. expected for the active block.
6. **-1n habit status.** Show which -1n habits are done/undone for today. Compact format (checkmarks/x's).
7. **Tracking gap detector.** Flag if there's untracked time > 15 min in the last 2 hours.

### P5: "One glance" completeness

Once P1-P4 ship, evaluate what's still missing to fully replace opening Chrome/Excel/Todoist for status checks. Candidates:

- **Next Todoist task** (from `/next` logic): show the top task below the calendar event
- **Daily points total** vs. target (from Neon 0分)
- **Unread comms count** (Gmail unread, iMessage unanswered, Slack mentions): a single badge, not the content
- **Toggl hours total** for the day vs. waking hours elapsed
- **Build order progress**: how many -1g blocks are done vs. planned

The goal is zero app-switching for status. Content creation (writing emails, editing docs) still happens in other apps; tg-tui is read-only status + timer control.

## Ritual integration

Completion events trigger spiritual prompts. When SIGUSR1 fires (task completed externally via `/did`), tg-tui flashes ☀️ in purple (`#aa00ff`) for 6 seconds. This reinforces the habit loop: **work → complete → pause → pray → next**. The flash is the cue to stop, breathe, and reconnect before picking up the next task.

## Evolving toward task-centric

The 60-100 task daily inventory is the real bottleneck, not time tracking. The TUI should make selecting and crossing off tasks as frictionless as possible. Key evolution:

- **Task queue overlay** (P2.5): Show top 5-7 tasks from task-queue.json below the time view. Press a number to start a timer for that task. Collapses the dtd→tg two-step into one.
- **Points accumulator**: Show today's total 分 in the header. Seeing the number climb is motivating.
- **Smart next suggestion**: After stopping a timer, briefly flash the highest-value unstarted task. One key to accept.

The end state: tg-tui is both the cockpit (status) and the control stick (task switching). dtd remains available for deep browsing/filtering, but the common path (pick next, start, complete, pick next) lives entirely in tg-tui.

## Non-goals

- Not a stats dashboard (that's jm-personal-dash on ix:5558)
- Not a full task manager (deep filtering/editing stays in Todoist + dtd)
- Not a planning tool (that's /0g, /-1g)
- No historical analysis; today only

## Data sources

| Source | Current | Planned |
|--------|---------|---------|
| Toggl API | yes (via toggl_api) | no change |
| Google Calendar | yes (via gcal_client.py) | no change |
| Outlook/Graph | no | P1 wire-up |
| Neon Excel (0分, 0₦) | no | P2-P4 (via ix-osa.sh or direct read) |
| Todoist | no | P5 (via MCP or REST API) |
| Gmail/iMessage/Slack | no | P5 (badge count only) |
