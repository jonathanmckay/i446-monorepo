#!/usr/bin/env bash
# Auto-fixing wrapper for imsg.py
# On crash: captures traceback, asks Claude to fix the file, then retries.

SCRIPT="$HOME/i446-monorepo/tools/ibx/imsg.py"
MAX_RETRIES=3
ERR_FILE=$(mktemp /tmp/imsg_error.XXXXXX)

cleanup() { rm -f "$ERR_FILE"; }
trap cleanup EXIT

for attempt in $(seq 1 $MAX_RETRIES); do
    python3 "$SCRIPT" 2>"$ERR_FILE"
    EXIT_CODE=$?

    # Clean exit or user Ctrl+C (no traceback) — done
    [[ $EXIT_CODE -eq 0 ]] && break
    ERROR=$(cat "$ERR_FILE")
    [[ -z "$ERROR" ]] && break

    if [[ $attempt -eq $MAX_RETRIES ]]; then
        echo ""
        echo "imsg failed after $MAX_RETRIES attempts. Last error:"
        echo "$ERROR"
        break
    fi

    echo ""
    echo "━━ imsg crashed (attempt $attempt/$MAX_RETRIES) — asking Claude to fix... ━━"
    echo ""

    claude -p "Fix this Python traceback in ~/i446-monorepo/tools/ibx/imsg.py. Edit the file directly to resolve the error. No explanation needed.

$ERROR" --allowedTools "Read,Edit,Grep"

    echo ""
    echo "Fix applied. Retrying imsg..."
    echo ""
    sleep 0.5
done
