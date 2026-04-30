#!/bin/bash
# SessionStart hook — captures the origin of a Claude Code session.
# Writes a sidecar JSON file alongside the session's .jsonl, recording
# whether the session was launched locally or via SSH (e.g. phone via Termius).
#
# Stdin: Claude Code hook payload (includes session_id, transcript_path).
# Output: <transcript_path with .jsonl replaced by .origin>
# Exits 0 always — must never block session start.

input=$(cat 2>/dev/null)
transcript=$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null)

[ -z "$transcript" ] && exit 0

origin_path="${transcript%.jsonl}.origin"
ts=$(date -u +%FT%TZ)

if [ -n "$SSH_CONNECTION" ]; then
    client_ip=$(printf '%s' "$SSH_CONNECTION" | awk '{print $1}')
    # Resolve Tailscale device name (e.g. imago = iPhone, straylight-refit = laptop).
    # Best-effort: empty string if tailscale not installed or IP is non-Tailscale.
    client_device=$(tailscale whois "$client_ip" 2>/dev/null | awk '/^  Name:/{print $2; exit}' | cut -d. -f1)
    printf '{"client":"ssh","ssh_connection":"%s","client_ip":"%s","client_device":"%s","ts":"%s"}\n' \
        "$SSH_CONNECTION" "$client_ip" "$client_device" "$ts" > "$origin_path"
else
    printf '{"client":"local","ts":"%s"}\n' "$ts" > "$origin_path"
fi

exit 0
