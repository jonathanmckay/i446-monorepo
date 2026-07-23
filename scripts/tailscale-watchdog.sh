#!/bin/bash
# tailscale-watchdog.sh — keep the Straylight → Ix path (ssh + excel-http)
# alive, self-heal what it can, and ALARM only on what it can't.
#
# Detected failure modes (2026-07-23 rewrite — the original only grepped for
# the MagicSock wedge, so "Tailscale is stopped." logged `ok` every 5 minutes
# through two full outages, 07-21 and 07-23, while Neon writes failed):
#   stopped — tailscaled/backend not running → `tailscale up`
#   wedge   — "MagicSock function ... is not running" → restart Tailscale.app
#   probe   — status LOOKS fine but `ssh ix` fails end-to-end → up, then restart
#
# The END-TO-END ssh probe is the real health signal; status text is only used
# to pick the cheapest remediation. After any recovery (or on a healthy tick),
# a non-empty ix write queue is drained via ix-drain-queue.sh so writes that
# failed during an outage replay automatically instead of waiting for a human.
#
# Alerting: persistent failures append to vault/z_ibx/alerts.jsonl (shared
# convention with git-autopush-healthcheck / dream-launch) AND fire a macOS
# notification with sound. Recovery + drain outcomes are notified quietly.
#
# Exit 0 = healthy or fixed, exit 1 = remediation failed (alarm raised).

ALERTS="$HOME/vault/z_ibx/alerts.jsonl"
HEALTH_LOG="$HOME/i446-monorepo/scripts/.tailscale-watchdog-health.log"
QUEUE="$HOME/.claude/ix-write-queue.jsonl"
DRAIN="$HOME/.claude/skills/_lib/ix-drain-queue.sh"
HOST="$(hostname -s)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# launchd has a bare PATH; prefer the app binary (proven to accept `up`
# without sudo), fall back to the CLI.
TS="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
[ -x "$TS" ] || TS="/usr/local/bin/tailscale"

mkdir -p "$(dirname "$ALERTS")"
touch "$HEALTH_LOG"

log() { echo "[$(date '+%F %T')] $*" >> "$HEALTH_LOG"; }

emit_alert() {
  local reason="$1" detail="$2"
  printf '{"ts":"%s","host":"%s","tool":"tailscale-watchdog","severity":"warning","reason":"%s","detail":"%s"}\n' \
    "$NOW" "$HOST" "$reason" "$detail" >> "$ALERTS"
  log "ALERT $reason — $detail"
}

notify() {  # notify <title> <body> [sound]
  local sound=""
  [ -n "$3" ] && sound=" sound name \"$3\""
  osascript -e "display notification \"$2\" with title \"Tailscale watchdog\" subtitle \"$1\"$sound" 2>/dev/null || true
}

probe() { ssh -o ConnectTimeout=5 -o BatchMode=yes ix true 2>/dev/null; }

drain_queue() {
  [ -s "$QUEUE" ] || return 0
  local n
  n=$(wc -l < "$QUEUE" | tr -d ' ')
  log "draining $n queued ix write(s)"
  if bash "$DRAIN" >> "$HEALTH_LOG" 2>&1; then
    notify "queue drained" "$n queued Neon write(s) replayed to Ix"
  else
    emit_alert "queue_drain_failed" "ix reachable but $n queued write(s) failed to replay"
    notify "queue drain FAILED" "$n Neon write(s) still queued — needs a look" "Basso"
  fi
}

STATUS="$("$TS" status 2>&1)"
FIX=""
if echo "$STATUS" | grep -qi "stopped"; then
  FIX="stopped"
elif echo "$STATUS" | grep -qi "MagicSock function.*is not running"; then
  FIX="wedge"
elif ! probe; then
  FIX="probe_failed"
fi

if [ -z "$FIX" ]; then
  log "ok"
  drain_queue
  exit 0
fi

log "$FIX detected — remediating"
case "$FIX" in
  stopped)
    "$TS" up --timeout=15s >/dev/null 2>&1
    sleep 3
    ;;
  wedge|probe_failed)
    # probe_failed with clean status: try the cheap `up` first, then the
    # full app restart that clears the MagicSock wedge.
    "$TS" up --timeout=15s >/dev/null 2>&1
    sleep 3
    if ! probe; then
      osascript -e 'quit app "Tailscale"' >/dev/null 2>&1
      sleep 2
      open -a "Tailscale" >/dev/null 2>&1
      sleep 8
      "$TS" up --timeout=15s >/dev/null 2>&1
      sleep 2
    fi
    ;;
esac

if probe; then
  log "fixed — $FIX remediated, ssh ix healthy"
  notify "recovered" "$FIX auto-fixed; Ix reachable again"
  drain_queue
  exit 0
fi

emit_alert "ix_unreachable_persisted" "$FIX: remediation ran but ssh ix still fails — Neon writes are failing"
notify "IX UNREACHABLE" "auto-fix failed ($FIX) — Neon writes are failing until this is fixed" "Basso"
exit 1
