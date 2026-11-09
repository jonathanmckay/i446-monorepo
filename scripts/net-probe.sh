#!/bin/bash
# net-probe.sh — one-minute connectivity record for this Mac, so "was the
# internet bad between 11:00 and 12:00?" has an answer after the fact.
#
# Why (2026-11-09): the fuchikoma hotspot was unusable for ~58 min and nothing
# on the laptop had recorded it; the only trace was two tailscale-watchdog
# probe failures and a fresh DHCP lease. One row per tick is cheap and gives
# the history: SSID, gateway, loss and latency to 1.1.1.1, HTTPS time to the
# Anthropic API, and en0 throughput since the previous tick (who-is-hogging
# evidence when the link is a phone's cellular uplink).
#
# Alerting: loss >= LOSS_ALERT_PCT (or HTTPS failing) for DEGRADED_TICKS
# consecutive ticks appends an alert to vault/z_ibx/alerts.jsonl (same
# convention as tailscale-watchdog) and fires a macOS notification, throttled
# to one per hour; recovery is logged and resets the throttle. Nothing is
# remediated here: the link is a phone's or a router's, not this Mac's.
#
# Log: ~/Library/Logs/net-probe.log  (one TSV row per tick, header on create)
#   ts  ssid  gateway  loss_pct  rtt_avg_ms  https_s  in_KBps  out_KBps  verdict
# Exit 0 always (a launchd tick must never flap).

LOG="${NET_PROBE_LOG:-$HOME/Library/Logs/net-probe.log}"
ALERTS="$HOME/vault/z_ibx/alerts.jsonl"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/jm"
BYTES_STAMP="$STATE_DIR/net-probe.bytes"       # "<epoch> <ibytes> <obytes>" from the last tick
BAD_STAMP="$STATE_DIR/net-probe.badticks"      # consecutive degraded ticks
ALERT_STAMP="$STATE_DIR/net-probe.alerted"     # epoch of last alert
LOSS_ALERT_PCT="${LOSS_ALERT_PCT:-40}"
DEGRADED_TICKS="${DEGRADED_TICKS:-3}"
ALERT_EVERY_S=3600
IFACE="${NET_PROBE_IFACE:-en0}"
HTTPS_URL="${NET_PROBE_URL:-https://api.anthropic.com/}"
PING="${NET_PROBE_PING:-/sbin/ping}"
HOST="$(hostname -s)"

mkdir -p "$STATE_DIR" "$(dirname "$LOG")" "$(dirname "$ALERTS")"
now_s=$(date +%s)
ts="$(date '+%F %T')"

[ -s "$LOG" ] || printf 'ts\tssid\tgateway\tloss_pct\trtt_avg_ms\thttps_s\tin_KBps\tout_KBps\tverdict\n' >> "$LOG"

ssid=$(ipconfig getsummary "$IFACE" 2>/dev/null | awk -F' : ' '/^ *SSID/{print $2; exit}')
ssid=${ssid:--}
gw=$(netstat -rn -f inet 2>/dev/null | awk -v i="$IFACE" '$1=="default" && $NF==i {print $2; exit}')
gw=${gw:--}

# 5 pings, 0.2 s apart, 2 s wait each: ~3 s worst case.
pout=$("$PING" -c 5 -i 0.2 -W 2000 1.1.1.1 2>/dev/null)
loss=$(echo "$pout" | awk '/packet loss/{for(i=1;i<=NF;i++) if($i ~ /%$/) {sub(/%$/,"",$i); print $i; exit}}')
loss=${loss:-100}
rtt=$(echo "$pout" | awk -F'/' '/round-trip/{print $5}')
rtt=${rtt:--}

https=$(curl -o /dev/null -s -w '%{time_total}' --max-time 8 "$HTTPS_URL" 2>/dev/null || echo fail)
[ -n "$https" ] || https=fail

# Throughput since the previous tick, from interface byte counters.
read -r ib ob <<< "$(netstat -ib 2>/dev/null | awk -v i="$IFACE" '$1==i && $7 ~ /^[0-9]+$/ {print $7, $10; exit}')"
in_kbps=-; out_kbps=-
if [ -n "$ib" ] && [ -s "$BYTES_STAMP" ]; then
  read -r p_s p_ib p_ob < "$BYTES_STAMP"
  dt=$(( now_s - p_s ))
  if (( dt > 0 && ib >= p_ib && ob >= p_ob )); then
    in_kbps=$(( (ib - p_ib) / dt / 1024 ))
    out_kbps=$(( (ob - p_ob) / dt / 1024 ))
  fi
fi
[ -n "$ib" ] && echo "$now_s $ib $ob" > "$BYTES_STAMP"

verdict=ok
if [ "$ssid" = "-" ] && [ "$gw" = "-" ]; then
  verdict=offline
elif [ "$https" = "fail" ] || (( ${loss%.*} >= LOSS_ALERT_PCT )); then
  verdict=degraded
fi

printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$ts" "$ssid" "$gw" "$loss" "$rtt" "$https" "$in_kbps" "$out_kbps" "$verdict" >> "$LOG"

emit_alert() {
  printf '{"ts":"%s","host":"%s","tool":"net-probe","severity":"warning","reason":"%s","detail":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$HOST" "$1" "$2" >> "$ALERTS"
}
notify() { osascript -e "display notification \"$2\" with title \"net-probe\" subtitle \"$1\"" 2>/dev/null || true; }

bad=$(cat "$BAD_STAMP" 2>/dev/null || echo 0)
if [ "$verdict" = ok ]; then
  if (( bad >= DEGRADED_TICKS )); then
    notify "recovered" "link ok again on $ssid after $bad degraded minute(s)"
  fi
  echo 0 > "$BAD_STAMP"
  rm -f "$ALERT_STAMP"
  exit 0
fi

# Asleep-with-the-lid-closed looks like `offline`; only a live-but-bad link counts.
[ "$verdict" = offline ] && exit 0

bad=$(( bad + 1 ))
echo "$bad" > "$BAD_STAMP"
(( bad >= DEGRADED_TICKS )) || exit 0
last_alert=$(cat "$ALERT_STAMP" 2>/dev/null || echo 0)
(( now_s - last_alert >= ALERT_EVERY_S )) || exit 0
detail="$ssid: loss ${loss}% rtt ${rtt}ms https ${https}s for ${bad} consecutive minutes"
emit_alert "link_degraded" "$detail"
notify "LINK DEGRADED" "$detail"
echo "$now_s" > "$ALERT_STAMP"
exit 0
