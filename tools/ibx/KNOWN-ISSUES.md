# ibx — Known Issues & Won't-Fix

Behaviors that look like bugs but aren't, to avoid re-investigating.

## Not-a-Bug

### iMessage blue dots on phone but ibx shows empty
**Date:** 2026-04-09
**Symptom:** iPhone Messages shows 3 blue-dot unread threads, ibx shows 0 iMessage items.
**Root cause:** iCloud Messages sync lag. The Mac `chat.db` receives the message, ibx processes it and writes the timestamp to `processed.json`, but the phone's read status hasn't synced yet.
**How to verify:** Check `~/.config/imsg/processed.json` timestamps vs `latest_date` from `chat.db`. If they match, ibx already handled it.
**Resolution:** Not a bug. Wait for iCloud sync or open Messages.app on Mac to trigger sync.

---

## Won't-Fix

_(none yet)_

---

## Fixed (for reference)

### Single-char attributedBody false positive (2026-04-09)
**Symptom:** iMessage body shows just "4" instead of full restaurant promo text.
**Root cause:** `extract_attributed_text` Strategy 1 returned a 1-byte TypedStream candidate before reaching the regex fallback.
**Fix:** Skip candidates with `len < 2` in the raw-bytes scanner (imsg.py:117).

### ibx crashes on quit (2026-04-09)
**Symptom:** Typing `q` triggers "ibx0 crashed (attempt 1/3) — asking Claude to fix..."
**Root cause:** Wrapper only treated exit code 0 as clean. Exit code 2 (user quit) fell through to crash handler.
**Fix:** `ibx_all_wrapper.sh` line 19: `[[ $EXIT_CODE -eq 0 || $EXIT_CODE -eq 2 ]] && break`

### Triage misclassifies property management emails (2026-04-09)
**Symptom:** "Cornerstone Apartments :: Asset Manager Introduction" removed from inbox by triage.
**Root cause:** Haiku classifier treated it as info-only despite being a business email.
**Fix:** Added "property management emails" to the "response" category in the classifier prompt (ibx.py:224).

### Auto-sign sender email mismatch (2026-04-09)
**Symptom:** Countersign emails from Andrea Perez not auto-signed.
**Root cause:** Config had `andie@m5c7.com`, actual email is `andrea@m5c7.com`.
**Fix:** Updated `config.py` AUTOSIGN_SENDERS.

### Teams workiq meta-commentary in message body (2026-04-09)
**Symptom:** Teams DM body shows "If you want, I can rerun this scoped to a time window..."
**Root cause:** Parser appended workiq AI commentary as message body.
**Fix:** Added break condition for workiq meta-phrases in `teams_workiq.py`.
