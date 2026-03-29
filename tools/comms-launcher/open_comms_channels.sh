#!/usr/bin/env bash
set -eo pipefail

# Opens communication tools in specific Chrome profiles.
# Update profile IDs/URLs below to match your setup.

declare -a TARGETS=(
  "Profile 5|https://mail.google.com/mail/u/0/#inbox|Gmail Personal"
  "Profile 11|https://mail.google.com/mail/u/0/#inbox|Gmail Work"
  "Profile 1|https://outlook.office.com/mail/|Outlook"
  "Profile 1|https://teams.microsoft.com/|Teams"
  "Profile 5|https://messages.google.com/web/conversations|Messages"
  "Profile 5|https://web.whatsapp.com/|WhatsApp"
)

chrome_app="/Applications/Google Chrome.app"
if [[ ! -d "$chrome_app" ]]; then
  echo "Google Chrome not found at $chrome_app"
  exit 1
fi

declare -a opened_profiles=()

for target in "${TARGETS[@]}"; do
  IFS='|' read -r profile url label <<< "$target"

  profile_seen=0
  for existing_profile in "${opened_profiles[@]}"; do
    if [[ "$existing_profile" == "$profile" ]]; then
      profile_seen=1
      break
    fi
  done

  if [[ "$profile_seen" -eq 0 ]]; then
    echo "Opening $label in $profile (new window)"
    open -na "$chrome_app" --args --profile-directory="$profile" --new-window "$url"
    opened_profiles+=("$profile")
  else
    echo "Opening $label in $profile (tab)"
    open -na "$chrome_app" --args --profile-directory="$profile" "$url"
  fi

  # Small pause avoids race conditions while Chrome attaches tabs/windows.
  sleep 0.25
done
