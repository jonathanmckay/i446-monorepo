#!/bin/bash
# tailscale-watchdog.sh — auto-remediate the MagicSock wedge that blocks ssh
# to Ix (and other tailnet hosts) roughly daily.
#
# `tailscale status` periodically reports "MagicSock function ReceiveIPv4 is
# not running" while still showing peers as "active" — the local tailscaled
# thinks the tunnel is fine but new connections (ssh, etc.) time out. The only
# known fix is restarting Tailscale.app; this runs that fix automatically
# instead of waiting for a manual `/salat`-style failure to surface it.
#
# Cron/launchd-friendly: exit 0 = healthy or fixed, exit 1 = restart attempted
# but the wedge persisted.

ALERTS="$HOME/vault/z_ibx/alerts.jsonl"
HEALTH_LOG="$HOME/i446-monorepo/scripts/.tailscale-watchdog-health.log"
HOST="$(hostname -s)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "$(dirname "$ALERTS")"
touch "$HEALTH_LOG"

emit_alert() {
  local reason="$1" detail="$2"
  printf '{"ts":"%s","host":"%s","tool":"tailscale-watchdog","severity":"warning","reason":"%s","detail":"%s"}\n' \
    "$NOW" "$HOST" "$reason" "$detail" >> "$ALERTS"
  echo "[$(date '+%F %T')] ALERT $reason — $detail" >> "$HEALTH_LOG"
}

STATUS="$(tailscale status 2>&1)"

if ! echo "$STATUS" | grep -qi "MagicSock function.*is not running"; then
  echo "[$(date '+%F %T')] ok" >> "$HEALTH_LOG"
  exit 0
fi

echo "[$(date '+%F %T')] MagicSock wedge detected — restarting Tailscale.app" >> "$HEALTH_LOG"
osascript -e 'quit app "Tailscale"' >/dev/null 2>&1
sleep 2
open -a "Tailscale" >/dev/null 2>&1
sleep 8

STATUS_AFTER="$(tailscale status 2>&1)"
if echo "$STATUS_AFTER" | grep -qi "MagicSock function.*is not running"; then
  emit_alert "magicsock_wedge_persisted" "restarted Tailscale.app but MagicSock warning is still present"
  osascript -e 'display notification "Restart did not clear the MagicSock warning — needs a look" with title "Tailscale watchdog" subtitle "wedge persisted" sound name "Basso"' 2>/dev/null || true
  exit 1
fi

echo "[$(date '+%F %T')] fixed — Tailscale.app restart cleared the MagicSock warning" >> "$HEALTH_LOG"
exit 0
