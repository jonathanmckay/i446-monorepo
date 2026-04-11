# ibx0 — Design Principles

## Core Principle

**If it shows up in one of your inboxes in its primary "todo" location, it should show up in ibx0.**

ibx0 is the single unified queue. If Outlook Focused Inbox, Gmail Inbox, iMessage, Slack DMs, or Teams chats show something as needing attention, ibx0 must show it too.

## Source-specific rules

### Outlook (via Agency MCP / Graph API)
- **Time window**: last 24h by `receivedDateTime`
- **Read filter**: `isRead eq false` — only truly unread emails
- **Skip own sent emails**: filter by sender address
- **Skip calendar noise**: Accepted/Declined/Canceled/Tentative/Updated/Forwarded meeting responses
- **Skip bridge emails**: auto-delete any `[IBX]` prefixed emails (legacy Gmail bridge)
- **Archive = DeleteMessage**: marks processed locally + deletes from mailbox (moves to Deleted Items, recoverable)
- **Trade-off**: Snoozed emails older than 24h won't appear (acceptable — snooze is rare)

### Gmail (via Gmail API)
- Fetches unread Inbox messages
- Triage runs first (NRN emails → no-response-needed label)
- Archive = remove INBOX label

### iMessage (via macOS chat.db)
- Fetches recent threads with unread messages
- Archive = mark thread as processed locally

### Slack (via Slack API)
- Fetches recent DM/MPIM threads from last 7 days
- Archive = mark as read via Slack API

### Teams (via workiq)
- Fetches recent 1:1 DMs (workiq NL query, read-only)
- Filters: skip own messages, skip group chats, skip empty messages
- Archive = mark processed locally (instant, no server call)
- Reply = opens Teams in browser (workiq can't send)
- **Item ID**: `teams:{sender}:{message_preview}` — no link hash (workiq returns inconsistent URLs)

## Dedup / Processing

- `processed.json` (per source) is the source of truth for "have I handled this in ibx0?"
- Items in `processed.json` never reappear regardless of source state
- Archive/reply/delete all write to `processed.json`

## Response time tracking

- `record_fetch()` logs when ibx0 first sees an item
- `record_action()` logs when the user acts (reply/archive)
- Delta = wall-to-wall response time (using actual receive timestamp from API when available)
- Stored in `~/.config/{outlook,teams}/response_times.db`
- Consumed by `gen_email_stats.py` → dashboard
