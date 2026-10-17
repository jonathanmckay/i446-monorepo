#!/bin/sh
# ix-tailnet-down.sh — exit 0 when ix is NOT reachable over the tailnet.
# Used by ~/.ssh/config:  Match host ix exec "~/i446-monorepo/scripts/ix-tailnet-down.sh"
# so `ssh ix` falls back to ix.local (LAN) during a tailnet outage. 2026-10-17:
# ix's Tailscale node key expired at 16:00 the day before and every Neon write
# from Straylight (did-fast → excel-http over `ssh ix`) failed for 14 hours while
# ix sat healthy two metres away on the LAN.
# IX_TAILNET_FORCE=up|down overrides the probe (tests).
case "${IX_TAILNET_FORCE:-}" in
  up) exit 1 ;;
  down) exit 0 ;;
esac
if nc -z -G 2 ix.tail9c51d5.ts.net 22 >/dev/null 2>&1; then
  exit 1   # tailnet path answers: keep the tailnet HostName
fi
exit 0     # tailnet down: use the LAN name
