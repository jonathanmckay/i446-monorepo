#!/usr/bin/env bash
# 05-urgent-sweep.sh — Hourly urgent email/Teams detection via workiq
# Sends macOS notifications with clickable Outlook links.
#
# Usage:  ./05-urgent-sweep.sh
# Cron:   0 6-18 * * 1-5 ~/i446-monorepo/scripts/workiq/05-urgent-sweep.sh

set -euo pipefail

PROMPT='List any unread emails or Teams messages I received in the past hour that meet ANY of these criteria:
1. Marked as High Priority / High Importance
2. Subject contains "Urgent", "Action Required", "ASAP", or "Time Sensitive"
3. Sent by someone with a C-level or VP+ job title (CVP, CTO, CEO, President, EVP, VP)
4. Sent by my manager or skip-level manager

For EACH match, output exactly this format (one block per message, separated by blank lines):
TYPE: email or teams
FROM: sender name
SUBJECT: subject line
SUMMARY: one sentence summary of what they need
LINK: direct Outlook or Teams link to the message

If nothing matches, output exactly: NONE'

RESPONSE=$(workiq ask -q "$PROMPT" 2>/dev/null)

# Exit silently if no urgent items
if echo "$RESPONSE" | grep -qiE "^NONE$|no urgent|nothing matches|no unread.*match|didn.t find"; then
    exit 0
fi

# Parse each block and send a notification
echo "$RESPONSE" | awk -v RS='\n\n' '{print}' | while IFS= read -r block; do
    # Skip empty blocks
    [ -z "$block" ] && continue

    from=$(echo "$block" | grep -i '^FROM:' | sed 's/^FROM: *//' | head -1)
    subject=$(echo "$block" | grep -i '^SUBJECT:' | sed 's/^SUBJECT: *//' | head -1)
    summary=$(echo "$block" | grep -i '^SUMMARY:' | sed 's/^SUMMARY: *//' | head -1)
    link=$(echo "$block" | grep -i '^LINK:' | sed 's/^LINK: *//' | head -1)

    # Also try to extract markdown-style links [N](url) if structured format wasn't used
    if [ -z "$link" ]; then
        link=$(echo "$block" | grep -oE 'https://outlook\.office365?\.com/[^ )]+' | head -1)
    fi
    if [ -z "$link" ]; then
        link=$(echo "$block" | grep -oE 'https://teams\.microsoft\.com/[^ )]+' | head -1)
    fi

    # Need at least a from or subject to notify
    if [ -z "$from" ] && [ -z "$subject" ]; then
        # Fallback: if workiq gave prose instead of structured output, notify with raw text
        if echo "$block" | grep -qiE 'urgent|priority|immediate|action required'; then
            terminal-notifier \
                -title "⚡ Urgent Email" \
                -message "$(echo "$block" | head -3 | tr '\n' ' ')" \
                -sound default \
                ${link:+-open "$link"} \
                -group "workiq-urgent" 2>/dev/null
        fi
        continue
    fi

    title="⚡ ${from:-Unknown sender}"
    message="${subject:-(no subject)}"
    [ -n "$summary" ] && message="$message — $summary"

    args=(-title "$title" -message "$message" -sound default -group "workiq-urgent")
    [ -n "$link" ] && args+=(-open "$link")

    terminal-notifier "${args[@]}" 2>/dev/null
done
