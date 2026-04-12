# Feature: Single-line updating status during ibx0 fetch

## Summary
Replace the multi-line startup output with a single line that updates in-place as each source connects. Instead of 15+ lines of status, show one line like: `Gmail ✓ 3 | iMsg ✓ 0 | Slack ... | Outlook ✓ 2 | Teams ✓ 0`

## Design

### Approach
Use Rich's `Live` display to render a single updating line. All fetch functions stop printing directly and instead update a shared `_fetch_status` dict. A `Live` context in the main thread re-renders the line whenever the dict changes.

Status flow per source:
- `...` → connecting/fetching
- `✓ N` → done, N items to review
- `✗` → error

Final line persists after all sources complete (Live stops, line stays).

### Files to change
- `tools/ibx/ibx0.py` — add `_fetch_status` dict, `_status_line()` renderer, `_update_status()` helper; replace `console.print` in fetch_emails/fetch_imsgs/fetch_slack with `_update_status` calls; wrap the concurrent fetch block with `Live`

### Files to NOT change
- fetch_outlook/fetch_teams — these are simpler (single call, no mid-stream output), just update status before/after
- Test files for other modules
- ibx.py, outlook_agency.py, etc.

## Implementation steps
1. Add `_fetch_status: dict[str, str]` and `_live: Live | None` module-level vars
2. Add `_update_status(source, msg)` that sets `_fetch_status[source]` and triggers Live refresh
3. Add `_status_line()` that renders the dict as a single Rich Text line
4. Replace all `console.print` in fetch_emails with `_update_status("Gmail", ...)` calls
5. Replace all `console.print` in fetch_imsgs with `_update_status("iMsg", ...)` calls
6. Replace all `console.print` in fetch_slack with `_update_status("Slack", ...)` calls
7. Update fetch_outlook/fetch_teams wrappers to call `_update_status` before/after
8. Wrap the concurrent fetch section with `Live` context, stop it after drain

## Test plan
- [ ] `test_fetch_functions_no_direct_console_print` — fetch_* functions must not call console.print (except error paths)
- [ ] `test_status_line_rendering` — verify _status_line() produces expected format from known dict
- [ ] `test_update_status_sets_dict` — verify _update_status modifies _fetch_status

## Risks / open questions
- Thread safety: `_fetch_status` is written from multiple threads — dict assignment is atomic in CPython (GIL), so this is safe
- Error messages (yellow warnings) could go to _update_status too, but they'd be lost when the line updates — may want to keep console.print for actual errors. Decision: keep console.print for errors/warnings only, use _update_status for progress.
