#!/usr/bin/env bash
# Auto-fixing wrapper for ibx0.py
# On crash: captures traceback, asks Claude to fix the file, then retries.
# On clean exit: waits POLL_INTERVAL seconds and re-runs (watch mode).

SCRIPT="$HOME/i446-monorepo/tools/ibx/ibx0.py"
MAX_RETRIES=3
POLL_INTERVAL=60  # seconds between re-fetches after inbox zero
ERR_FILE=$(mktemp /tmp/ibx_all_error.XXXXXX)

cleanup() { rm -f "$ERR_FILE"; }
trap cleanup EXIT

# IBX_AUTORUN=1 marks a re-run the 60s timer started rather than JM (Enter or
# the first launch): ibx0 then only refreshes the per-habit counts for dtd and
# never auto-closes an inbox habit (2026-11-12, rubber-duck: a TUI left open
# overnight would otherwise credit every habit at 04:00).
AUTORUN=""

while true; do
    for attempt in $(seq 1 $MAX_RETRIES); do
        IBX_AUTORUN="$AUTORUN" python3 "$SCRIPT" 2>"$ERR_FILE"
        EXIT_CODE=$?

        [[ $EXIT_CODE -eq 0 || $EXIT_CODE -eq 2 ]] && break
        ERROR=$(cat "$ERR_FILE")
        [[ -z "$ERROR" ]] && break

        if [[ $attempt -eq $MAX_RETRIES ]]; then
            echo ""
            echo "ibx0 failed after $MAX_RETRIES attempts. Last error:"
            echo "$ERROR"
            break
        fi

        echo ""
        echo "━━ ibx0 crashed (attempt $attempt/$MAX_RETRIES) — asking Claude to fix... ━━"
        echo ""

        claude -p "Fix this Python traceback in ~/i446-monorepo/tools/ibx/ibx0.py. Edit the file directly to resolve the error. No explanation needed.

$ERROR" --allowedTools "Read,Edit,Grep"

        echo ""
        echo "Fix applied. Retrying ibx0..."
        echo ""
        sleep 0.5
    done

    # Exit code 0 = inbox zero (re-poll). 1 = crash (re-poll). 2 = user quit (stop).
    LAST_EXIT=$EXIT_CODE
    [[ $LAST_EXIT -eq 2 ]] && break

    echo ""
    echo "── Inbox zero — checking again in ${POLL_INTERVAL}s  (Enter to refresh, Ctrl+C to quit) ──"
    if read -t "$POLL_INTERVAL" -s -n 1; then
        echo "  ↻ Refreshing now..."
        AUTORUN=""
    else
        AUTORUN=1
    fi
done
