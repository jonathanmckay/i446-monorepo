#!/usr/bin/env bash
# inbound wrapper: runs the unified interrupt queue TUI with auto-restart on crash.
# Same pattern as -2n_wrapper.sh and ibx0_wrapper.sh.

SCRIPT="$HOME/i446-monorepo/tools/ibx/inbound.py"

if [[ ! -f "$SCRIPT" ]]; then
    echo "inbound_wrapper: missing $SCRIPT" >&2
    exit 1
fi

MAX_RETRIES=3
ERR_FILE=$(mktemp /tmp/inbound_error.XXXXXX)

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
            echo "inbound failed after $MAX_RETRIES attempts. Last error:"
            echo "$ERROR"
            break
        fi

        echo ""
        echo "━━ inbound crashed (attempt $attempt/$MAX_RETRIES) — asking Claude to fix... ━━"
        echo ""

        claude -p "Fix this Python traceback in ~/i446-monorepo/tools/ibx/inbound.py. Edit the file directly to resolve the error. No explanation needed.

$ERROR" --allowedTools "Read,Edit,Grep"

        echo ""
        echo "Fix applied. Retrying inbound..."
        echo ""
        sleep 0.5
    done

    # Exit code 2 = user quit (stop). Otherwise restart.
    [[ $EXIT_CODE -eq 2 ]] && break

    echo ""
    echo "── inbound exited — restarting in 5s  (Ctrl+C to quit) ──"
    sleep 5
done
