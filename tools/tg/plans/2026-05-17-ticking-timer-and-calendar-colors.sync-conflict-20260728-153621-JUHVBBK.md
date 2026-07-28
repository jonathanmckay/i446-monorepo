# Feature: Ticking timer + calendar event colors

## Summary
Two visual improvements to tg-tui: (1) show the running timer with a live seconds counter so it feels alive, and (2) color future calendar events by their Neon domain category using the existing PROJECT_COLORS palette.

## Design

### Approach

**Ticking timer:** `render_current()` already recomputes elapsed on each render, and `ticker_clock` invalidates every 1s. The problem is `fmt_dur()` only shows hours/minutes. Add seconds display to the current timer line (e.g. "▶ work · i9  42m 15s"). Only show seconds for the current running timer, not elsewhere.

**Calendar colors:** Google Calendar events don't carry Neon domain codes. We need a mapping layer. Two strategies, used in order:
1. **Calendar name mapping**: Map calendar names to project codes (e.g. "Xbox" calendar → i9, "McKay Capital" → m5x2, "Personal" → xk87). This covers the majority of events.
2. **Keyword fallback**: For shared/default calendars, scan the event title for keywords (e.g. "1:1" or "standup" → i9).

The calendar-to-project mapping lives as a dict in tg-tui.py alongside PROJECT_COLORS.

Apply color in `_slot_label_gcal`, `render_detail`, and `render_evening` by returning the resolved project code and using `project_style()`.

### Files to change
- `tg-tui.py` — all changes (ticking display, calendar color mapping, render functions)

### Files to NOT change
- `gcal_client.py` — already returns calendar name in event dict; no changes needed
- `tg-fast.py` — unrelated CLI tool

## Implementation steps
1. Add `fmt_dur_seconds(seconds)` helper that shows "Xh Ym Zs" or "Ym Zs" — used only for current timer
2. Update `render_current()` to use seconds-level elapsed display
3. Add `CALENDAR_PROJECT_MAP` dict mapping calendar names → project codes
4. Add `EVENT_KEYWORD_MAP` dict for title-based fallback
5. Add `gcal_project_code(event)` function: tries calendar map, then keyword scan, returns code or None
6. Update `_slot_label_gcal()` to return project code (second tuple element) using `gcal_project_code`
7. Update `render_detail()` to use returned gcal project code for styling future slots
8. Update `render_evening()` to color events by their resolved project code

## Test plan
- [ ] Running timer shows seconds ticking in real-time
- [ ] Calendar events in detail band show project colors
- [ ] Evening section events show project colors
- [ ] Events with no mapping still render (white/default)
- [ ] Toggl past entries still render with correct colors (no regression)

## Risks / open questions
- Calendar name mapping needs to be populated from actual calendar names on the account. Will inspect gcal cache to get these.

## Result
- **Status:** Complete
- **Tests:** Syntax check passes. Visual verification needed (run tg-tui).
- **Changes:** `tg-tui.py` only. Added `fmt_dur_seconds`, `CALENDAR_PROJECT_MAP`, `EVENT_KEYWORD_MAP`, `gcal_project_code()`. Updated `render_current`, `_slot_label_gcal`, `render_detail`, `render_evening`.
- **Calendar mapping:** 3494 House/m5x2 Cal → m5x2, CAIS School → xk87, Habits → hcm, lx@m5c7.com/lxu888 → xk88, Calendar/gmail → infra. Keyword fallback for i9 (1:1, standup, etc.).
