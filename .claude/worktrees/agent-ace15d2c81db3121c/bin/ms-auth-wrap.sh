#!/usr/bin/env bash
# ms-auth-wrap.sh — run a command and, if it triggers a Microsoft EntraID
# interactive auth popup, fire a macOS notification telling the user which
# cron caller is responsible.
#
# Usage:
#   ms-auth-wrap.sh <label> <cmd> [args...]
#
# Side effects:
#   - Writes "<epoch> <label>" to ~/.ms-auth-caller (last caller wins).
#   - Appends invocations + auth events to ~/.ms-auth-history.log.
#   - On detected auth failure / interactive prompt, posts a terminal-notifier
#     notification: "🔐 Microsoft sign-in: <label>".

set -u
LABEL="${1:-unknown}"
shift || true

STAMP_FILE="$HOME/.ms-auth-caller"
HIST_FILE="$HOME/.ms-auth-history.log"
NOTIFIER="/opt/homebrew/bin/terminal-notifier"

ts() { date +"%Y-%m-%d %H:%M:%S"; }

printf "%s %s\n" "$(date +%s)" "$LABEL" > "$STAMP_FILE"
printf "[%s] START %s :: %s\n" "$(ts)" "$LABEL" "$*" >> "$HIST_FILE"

TMPOUT="$(mktemp -t msauth.XXXXXX)"
trap 'rm -f "$TMPOUT"' EXIT

"$@" 2>&1 | tee -a "$TMPOUT"
RC="${PIPESTATUS[0]}"

# Patterns that indicate AzureAuth tried (or had to try) an interactive prompt.
if grep -q -E -i \
   'AzureAuth|interactiveauthrequired|auth resolution failed|Failed to get EntraID token|login\.microsoftonline\.com|MSAL\.NetCore' \
   "$TMPOUT"; then
   printf "[%s] AUTH-EVENT %s\n" "$(ts)" "$LABEL" >> "$HIST_FILE"
   if [ -x "$NOTIFIER" ]; then
       "$NOTIFIER" \
           -title "🔐 Microsoft sign-in needed" \
           -subtitle "Caller: $LABEL" \
           -message "A Chrome login.microsoftonline.com popup was triggered by this cron job." \
           -group "ms-auth-$LABEL" \
           -sound default >/dev/null 2>&1 || true
   fi
fi

printf "[%s] END   %s rc=%s\n" "$(ts)" "$LABEL" "$RC" >> "$HIST_FILE"
exit "$RC"
