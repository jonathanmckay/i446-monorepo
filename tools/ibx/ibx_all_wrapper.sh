#!/usr/bin/env bash
# Auto-fixing wrapper for ibx_all.py
# On crash: captures traceback, asks Claude to fix the file, then retries.
# On clean exit: waits POLL_INTERVAL seconds and re-runs (watch mode).

SCRIPT="$HOME/i446-monorepo/tools/ibx/ibx_all.py"
MAX_RETRIES=3
POLL_INTERVAL=60  # seconds between re-fetches after inbox zero
ERR_FILE=$(mktemp /tmp/ibx_all_error.XXXXXX)

cleanup() { rm -f "$ERR_FILE"; }
trap cleanup EXIT

while true; do
    for attempt in $(seq 1 $MAX_RETRIES); do
        python3 "$SCRIPT" 2>"$ERR_FILE"
        EXIT_CODE=$?

        [[ $EXIT_CODE -eq 0 ]] && break
        ERROR=$(cat "$ERR_FILE")
        [[ -z "$ERROR" ]] && break

        if [[ $attempt -eq $MAX_RETRIES ]]; then
            echo ""
            echo "ibx-all failed after $MAX_RETRIES attempts. Last error:"
            echo "$ERROR"
            break
        fi

        echo ""
        echo "━━ ibx-all crashed (attempt $attempt/$MAX_RETRIES) — asking Claude to fix... ━━"
        echo ""

        claude -p "Fix this Python traceback in ~/i446-monorepo/tools/ibx/ibx_all.py. Edit the file directly to resolve the error. No explanation needed.

$ERROR" --allowedTools "Read,Edit,Grep"

        echo ""
        echo "Fix applied. Retrying ibx-all..."
        echo ""
        sleep 0.5
    done

    # Exit code 0 = inbox zero (re-poll). 1 = crash (re-poll). 2 = user quit (stop).
    LAST_EXIT=$EXIT_CODE
    [[ $LAST_EXIT -eq 2 ]] && break

    echo ""
    echo "── Inbox zero — checking again in ${POLL_INTERVAL}s  (Ctrl+C to quit) ──"
    sleep "$POLL_INTERVAL"
done
