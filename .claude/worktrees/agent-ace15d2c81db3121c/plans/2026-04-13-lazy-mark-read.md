# Feature: Lazy mark-as-read for Teams

## Summary
Stop opening Chrome tabs on every Teams archive/reply. Only trigger the expensive
mark-as-read (Chrome tab → Teams web) when we detect a stubborn unread: a message
that's in processed.json but still appears in the search results on next fetch.

## Design

### Approach
1. Remove `_mark_chat_read()` calls from `archive()` and `reply()` — these are the
   hot path, called on every user action.
2. Keep the retry-on-fetch path in `fetch_teams_items()` — this only fires when a
   processed item is *still in search results*, proving it's genuinely stuck as unread.
3. This is already implemented in fetch; we just need to remove the eager calls.

### Files to change
- `tools/ibx/teams_agency.py` — remove `_mark_chat_read` from `archive()` and `reply()`

### Files to NOT change
- `tools/ibx/ibx0.py` — no changes needed
- `tools/ibx/ibx0_wrapper.sh` — no changes needed

## Implementation steps
1. Remove `_mark_chat_read(chat_id)` from `archive()` — `teams_agency.py`
2. Remove `_mark_chat_read(chat_id)` from `reply()` — `teams_agency.py`
3. Update tests that check for `_mark_chat_read` in archive/reply

## Test plan
- [ ] archive() does NOT call _mark_chat_read
- [ ] reply() does NOT call _mark_chat_read
- [ ] fetch_teams_items() still retries _mark_chat_read for stubborn unreads
- [ ] Existing tests pass

## Risks / open questions
- None — the retry-on-fetch path is already proven to work
