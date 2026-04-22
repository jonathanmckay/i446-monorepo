#!/usr/bin/env bash
# -2n wrapper: runs the interrupt queue TUI with auto-restart on crash.
# Same pattern as ibx0_wrapper.sh.

SCRIPT="$HOME/i446-monorepo/tools/ibx/-2n.py"
MAX_RETRIES=3
ERR_FILE=$(mktemp /tmp/-2n_error.XXXXXX)

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
            echo "-2n failed after $MAX_RETRIES attempts. Last error:"
            echo "$ERROR"
            break
        fi

        echo ""
        echo "━━ -2n crashed (attempt $attempt/$MAX_RETRIES) — asking Claude to fix... ━━"
        echo ""

        claude -p "Fix this Python traceback in ~/i446-monorepo/tools/ibx/-2n.py. Edit the file directly to resolve the error. No explanation needed.

$ERROR" --allowedTools "Read,Edit,Grep"

        echo ""
        echo "Fix applied. Retrying -2n..."
        echo ""
        sleep 0.5
    done

    # Exit code 2 = user quit (stop). Otherwise restart.
    [[ $EXIT_CODE -eq 2 ]] && break

    echo ""
    echo "── -2n exited — restarting in 5s  (Ctrl+C to quit) ──"
    sleep 5
done
