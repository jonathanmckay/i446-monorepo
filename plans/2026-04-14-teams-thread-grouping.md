# Feature: Teams thread grouping in ibx0

## Summary
Consolidate multiple unread Teams messages from the same chat thread into a single card in ibx0. If 3 new messages arrive in 1 thread, show 1 card with all messages, count it as 1 item, and base response time on the most recent message.

## Design

### Approach
Group Teams search hits by `chat_id` in `teams_agency.fetch_teams_items()`. Use `chat_id` as the stable thread-level identity for queue dedup, and track individual message IDs only for processed-state bookkeeping.

Two identities per grouped card:
- **Thread identity:** `chat_id` → used for `_item_uid`, queue dedup, `seen_uids`
- **Representative message:** latest `item_id` → used for reply link, response-time analytics

### Files to change
- `tools/ibx/teams_agency.py` — group hits by chat_id, emit consolidated items
- `tools/ibx/ibx0.py` — update `_item_uid`, `do_archive`, `do_delete`, `do_reply`, `display_card` for grouped items
- `tools/ibx/test_teams_agency.py` — add tests for grouping behavior

### Files to NOT change
- `tools/ibx/teams_workiq.py` — legacy fallback, separate code path
- `tools/ibx/ibx.py` — email-only TUI, unrelated
- `tools/ibx/imsg.py`, `tools/ibx/slack.py` — other sources, unrelated

## Implementation steps
1. **teams_agency.py: group hits by chat_id** — After parsing all hits and filtering processed/legacy, group unprocessed items by `chat_id`. For each group with >1 message, sort chronologically, concatenate bodies, use latest message's date/link/sender as representative. Store `all_item_ids` list in `_data`.
2. **teams_agency.py: archive_all helper** — Add `archive_all(item_ids, chat_id)` that marks all individual message IDs as processed without recording response-time actions for each.
3. **ibx0.py: `_item_uid` for grouped teams** — Return `("teams_thread", chat_id)` when `chat_id` is available (grouped or single). This prevents duplicate cards when new messages arrive in the same thread between fetches.
4. **ibx0.py: `do_archive` / `do_delete`** — For teams items with `all_item_ids`, call `archive_all` to mark all message IDs processed.
5. **ibx0.py: `do_reply`** — After replying to a teams item, also mark all grouped message IDs processed (not just the representative).
6. **ibx0.py: `display_card`** — Show message count for grouped teams cards, e.g. "(3 messages)".
7. **ibx0.py: response time** — `received_at` uses the latest message's date (already handled by using latest message as representative).
8. **tests** — Add regression tests for grouping, archive-all, and UID stability.

## Test plan
- [ ] Grouped items use chat_id-based UID, not msg_id-based
- [ ] Multiple messages in same chat_id produce exactly 1 item
- [ ] Single-message chats still work normally
- [ ] `archive_all` marks all individual message IDs as processed
- [ ] `all_item_ids` is present in grouped items' `_data`
- [ ] Body of grouped item contains all messages chronologically
- [ ] Date of grouped item is the latest message's date

## Risks / open questions
- `size=25` search limit is message-based, not thread-based. A noisy chat could crowd out others. Acceptable for now; can increase later if needed.
- If a new message arrives in a thread between fetch and user action, the next fetch will still dedup correctly via chat_id UID.

## Result
- **Status:** Complete
- **Tests:** 9 new tests, 20 total passing
- **Notes:** Used `chat_id` as stable thread UID per rubber-duck critique. Added `archive_all`/`delete_all` helpers. Reply path also marks all grouped IDs processed.
