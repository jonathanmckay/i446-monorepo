# Feature: quick-add button in dtd web

## Summary
Add a floating "+" button in the bottom-right corner of dtd web
(`tools/dtd/dtd.py`) that opens a small inline input for typing a new task,
creates it in Todoist (Inbox, due today — same defaults as `/todo`), and
refreshes the list to show it. Closes the existing backlog item "ability to
add tasks to dtd web (30) [30]" (Todoist id `6hG3v39469m4vMw6`).

## Design

### Approach
- Reuse the exact `(N)`/`[N]`/`@tag` syntax `/todo` already uses, parsed
  server-side with plain regex (dtd.py already has `_TIME`/`_VAL` compiled
  for parsing task content back out in `parse_est` — reuse the same patterns
  for parsing input, plus a new `@word` tag regex).
- **No LLM-based domain/estimate inference** — dtd.py is a plain Flask
  process with no model access, and guessing a fake `[N]`/domain server-side
  would put untrustworthy numbers into the point-tracking system. If the
  user wants time/points/domain, they type them explicitly
  (`buy milk (10) [5] @家`), exactly like `/todo`. Omitted modifiers are just
  omitted — no default injected. This is a deliberate v1 scope cut, not an
  oversight.
- New endpoint `POST /api/add` — parses, creates the task via a direct
  Todoist REST call (same token file `TODOIST_TOKEN_FILE` already used by
  `_todoist_completed_today`), due `today`, Inbox (no `project_id`), priority
  `p4`. Returns `{ok, content}` or `{ok: false, error}`.
- Frontend: fixed-position circular "+" button (bottom-right, above the safe
  area). Tap opens a bottom-sheet-style overlay with a single text input +
  Add/Cancel. Enter submits, Escape/tap-outside cancels. On success, calls
  the existing `load(true)` (same force-refresh path the ↻ button already
  uses) so the new task appears immediately, and shows the existing `toast()`
  for feedback. No new CSS framework, no new JS deps — matches the file's
  existing single-file, no-external-deps convention.
- Task-cache refresh after creation piggybacks on the frontend's existing
  `load(true)` call (which already triggers `build_tasks(force_refresh=True)`
  → `did-fast.py --refresh-cache` server-side) rather than adding a second,
  redundant refresh path inside `/api/add` itself.

### Files to change
- `tools/dtd/dtd.py` — add `_TAG` regex, `parse_add_input()` helper,
  `create_todoist_task()` helper, `POST /api/add` route, `+` button markup,
  overlay CSS, and the small amount of JS to wire it to `/api/add` +
  `load(true)`.

### Files to NOT change
- `tools/did/dtd.sh` (terminal dtd) — out of scope, it already has its own
  add-task path (`/todo`, `dtd-add.sh` region if any). This feature is web-only.
- `tools/did/did-fast.py` / `defer-fast.py` — no changes needed; task
  creation here is a direct, minimal Todoist POST, not routed through the
  did-fast pipeline (that pipeline is for *completing* tasks).

## Implementation steps
1. Add `_TAG` regex + `parse_add_input(raw: str) -> tuple[str, list[str]]`
   (content-with-(N)/[N]-preserved, labels-from-@tags) to `dtd.py`.
2. Add `create_todoist_task(content: str, labels: list[str]) -> dict` — POST
   to `https://api.todoist.com/api/v1/tasks` with `due_string: "today"`,
   `priority: 4` (Todoist API's `p4`==priority 1 — verify the correct
   integer value against the existing `/api/done` or did-fast.py convention
   before hardcoding).
3. Add `POST /api/add` Flask route wiring the two together.
4. Add the `+` button + overlay HTML/CSS to `PAGE`.
5. Add the JS: open/close overlay, submit handler → `fetch('/api/add', ...)`
   → on success `load(true)` + `toast()`, on failure `toast(err, true)`.

## Test plan
- [x] `parse_add_input("buy milk (10) [5] @家")` → content `"buy milk (10) [5]"`, labels `["家"]`
- [x] `parse_add_input("plain task")` → content `"plain task"`, labels `[]`
- [x] `parse_add_input("multi tag @i9 @foo thing")` → labels `["i9", "foo"]`, tags stripped from content
- [x] `POST /api/add` with empty content → 400
- [x] `POST /api/add` with valid content → calls Todoist with `due_string: today`, no `project_id` (Inbox), correct priority int
- [x] `POST /api/add` surfaces a Todoist API error as `{ok: false, error: ...}` rather than a raw 500

## Risks / open questions
- Todoist's REST v1 `priority` field: confirmed live against task
  `6hG8GqgFXw2PF7Fc` (a real `p4` task) — raw API value is `1`. Used `1`.
- No offline queueing — if the phone is offline, `/api/add` just fails with
  a toast, same as the existing swipe-to-complete `commit()` failure mode.
  Consistent with the rest of the app, not a regression.

## Result
- **Status:** Complete
- **Tests:** 6 new (`test_dtd_quick_add.py`), all passing; full `tools/dtd` suite 16/16
- **Live verification:** browser extension wasn't connected in this
  environment, so verified end-to-end against the real deployed server
  instead — created a real Todoist task through `/api/add`, confirmed it
  rendered correctly via `/api/tasks` (right color, right `(N) [N]`, right
  domain, landed in Inbox due today), then deleted the test artifact and
  refreshed the cache.
- **Deployed:** restarted `com.jm.dtd` on ix (`launchctl kickstart -k`).
- **Notes:** implementation matched the plan with no deviations.
