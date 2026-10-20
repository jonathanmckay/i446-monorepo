#!/usr/bin/env bash
# 卯 wakeup wrapper: runs the forced-linear -1₦ wakeup sequence with
# auto-restart on crash. Same pattern as inbound_wrapper.sh.

# ── Remote delegation: if on ix via SSH, run on Straylight instead ──
# Straylight has Excel (0n/Neon AppleScript) + work email which ix doesn't.
if [[ -n "$SSH_CLIENT" ]] && [[ "$(hostname)" == *"Mac-mini"* || "$(hostname)" == *"ix"* ]]; then
    STRAYLIGHT_HOST="192.168.1.53"
    if ssh -o ConnectTimeout=3 -o BatchMode=yes "$STRAYLIGHT_HOST" true 2>/dev/null; then
        echo "── delegating to Straylight (Excel + work email access) ──"
        exec ssh -t "$STRAYLIGHT_HOST" "bash ~/i446-monorepo/tools/ibx/wakeup_wrapper.sh"
    else
        echo "── Straylight unreachable, running locally on ix ──"
    fi
fi

SCRIPT="$HOME/i446-monorepo/tools/ibx/wakeup.py"

if [[ ! -f "$SCRIPT" ]]; then
    echo "wakeup_wrapper: missing $SCRIPT" >&2
    exit 1
fi

MAX_RETRIES=3
ERR_FILE=$(mktemp /tmp/wakeup_error.XXXXXX)

cleanup() { rm -f "$ERR_FILE"; }
trap cleanup EXIT

while true; do
    for attempt in $(seq 1 $MAX_RETRIES); do
        python3 "$SCRIPT" 2>"$ERR_FILE"
        EXIT_CODE=$?

        [[ $EXIT_CODE -eq 0 || $EXIT_CODE -eq 2 ]] && break
        ERROR=$(cat "$ERR_FILE")
        [[ -z "$ERROR" ]] && break

        if [[ $attempt -eq $MAX_RETRIES ]]; then
            echo ""
            echo "wakeup failed after $MAX_RETRIES attempts. Last error:"
            echo "$ERROR"
            break
        fi

        echo ""
        echo "━━ wakeup crashed (attempt $attempt/$MAX_RETRIES) — asking Claude to fix... ━━"
        echo ""

        claude -p "Fix this Python traceback in ~/i446-monorepo/tools/ibx/wakeup.py. Edit the file directly to resolve the error. No explanation needed.

$ERROR" --allowedTools "Read,Edit,Grep"

        echo ""
        echo "Fix applied. Retrying wakeup..."
        echo ""
        sleep 0.5
    done

    # Exit code 2 = user quit (stop). Sequence complete (0) also exits — the
    # wakeup run is one-shot, no idle loop to restart into.
    break
done
