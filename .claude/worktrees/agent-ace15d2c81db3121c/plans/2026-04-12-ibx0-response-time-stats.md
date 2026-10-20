# Feature: Response time stats after every reply in ibx0

## Summary
After sending a reply in ibx0, display the response time for that message and the running average response time for the day. Example output: `⏱ Response time: 23m · Day avg: 47m → 43m (12 replies)`

## Design

### Approach
Each item already stores a received timestamp in `_data.date` (ISO 8601 string for email/outlook) or `_data.thread.latest_date` (iMessage) or `ts` (Slack epoch). We'll:

1. Parse the received time from each item when it's normalized (store as `received_at` epoch float on the item dict)
2. Track a list of response times for the session in a module-level list `_response_times: list[float]` (minutes)
3. After every successful `do_reply`, compute `response_time = now - item["received_at"]`, append to `_response_times`, and print the stats line

The "day average" shows the before→after change so the user sees the impact of each reply.

### Received time extraction (per source)
- **email (Gmail):** `item["_data"]["email"]["date"]` — RFC 2822 or ISO string
- **outlook:** `item["_data"]["email"]["date"]` — ISO 8601 with Z suffix
- **iMessage:** `item["_data"]["thread"]["latest_date"]` — format like "apr 05, 01:30pm"
- **Slack:** `item["ts"]` — Unix epoch float
- **Teams:** `item["_data"]["email"]["date"]` — ISO 8601 (same as outlook)

### Files to change
- `tools/ibx/ibx0.py` — add `received_at` to normalize functions, add `_response_times` tracker, add `_print_response_stats()` helper, call it after every `do_reply`

### Files to NOT change
- `ibx.py`, `outlook_agency.py`, `teams_agency.py` — data sources stay untouched, we parse what they already provide
- `ibx0_wrapper.sh` — no change needed

## Implementation steps
1. Add `_response_times: list[float] = []` module-level variable — `ibx0.py` top
2. Add `_parse_received_at(item) -> float` helper that extracts epoch from any item type — `ibx0.py`
3. Set `item["received_at"]` in each `normalize_*` function by calling `_parse_received_at`
4. Add `_print_response_stats(item)` that computes response time, appends to `_response_times`, prints the stats line — `ibx0.py`
5. Call `_print_response_stats(item)` after every successful `do_reply` call (4 locations: r, R, p/P Claude reply, Claude action=reply)

## Test plan
- [x] `test_parse_received_at_gmail` — ISO date string → correct epoch
- [x] `test_parse_received_at_slack` — Slack ts float → correct epoch
- [x] `test_parse_received_at_missing` — missing date → falls back to 0.0
- [x] `test_response_stats_formatting` — verify output format with known times
- [x] `test_response_stats_average_update` — avg correctly updates after multiple replies

## Risks / open questions
- iMessage `latest_date` format may vary — need to handle parse failures gracefully (fallback to "unknown")
- Gmail `date` field is sometimes missing (ts=0.0) — skip response time display for those
