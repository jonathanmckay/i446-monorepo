#!/bin/bash
# prune-networks.sh — forget saved public Wi-Fi (hotels, airports, venues,
# conferences, cafes, corporate guest) so macOS stops auto-joining stale
# captive networks ahead of the hotspots (2026-10-27). Private-looking
# networks (homes, phones, MSFTCONNECT) and the three airline SSIDs stay.
# Skips whatever network is currently joined. Run with sudo, after
# `ipconfig setverbose 1` so the current SSID is readable.
#
# Known networks sync through iCloud Keychain: this forgets them on the
# iPhone too.

PUBLIC=(
  "Proper_Reserve" "Proper_Calabra" "#SFO FREE WIFI"
  "Woodmark Guest Internet" "WHotels_GUEST" "WHotels_PUBLIC"
  "Collection Guest FreeWifi" "EurostarTrainsWiFi" "MarriottBonvoy_Guest"
  "#hotspot.koelnmesse" "Telekom_FlyNet" "CDAResort" "GitHub Guest"
  "@yvrairport" "FourSeasons" "GitHub Events" "CES Wi-Fi" "Beach Club"
  "LadderCoffee_5"
)

current="$(ipconfig getsummary en0 | sed -n 's/^ *SSID : //p')"
for ssid in "${PUBLIC[@]}"; do
  if [[ "$ssid" == "$current" ]]; then
    echo "skip (currently joined): $ssid"
    continue
  fi
  networksetup -removepreferredwirelessnetwork en0 "$ssid"
done
echo "remaining:"
networksetup -listpreferredwirelessnetworks en0
