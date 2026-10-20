#!/usr/bin/env bash
# ibx_monitor — persistent inbox status display with tab coloring.
#
# Tab turns GREEN when items are pending (action required).
# Tab stays BLACK when inbox is zero.
# Press Enter to launch ibx inline; returns to monitoring after.
#
# Usage: bash ~/i446-monorepo/tools/ibx/ibx_monitor.sh

TERM_COLOR="$HOME/i446-monorepo/scripts/term-color.sh"
STATUS_PY="$HOME/i446-monorepo/tools/ibx/ibx_status.py"
IBX_SH="$HOME/i446-monorepo/tools/ibx/ibx_all_wrapper.sh"
POLL=30  # seconds between checks

# ── Helpers ───────────────────────────────────────────────────────────────────

jq_field() { python3 -c "import json,sys; d=json.load(sys.stdin); print(d['$1'])"; }

plural() { [ "$1" -eq 1 ] && echo "" || echo "s"; }

check_and_display() {
    local json total email imsg slack

    json=$(python3 "$STATUS_PY" 2>/dev/null) || json='{"email":0,"imsg":0,"slack":0,"total":0}'

    total=$(echo "$json" | jq_field total)
    email=$(echo "$json" | jq_field email)
    imsg=$(echo "$json" | jq_field imsg)
    slack=$(echo "$json" | jq_field slack)

    clear
    echo ""

    if [ "$total" -gt 0 ]; then
        bash "$TERM_COLOR" green
        printf "  \033[1;32m●\033[0m  \033[1m%d item%s pending\033[0m\n" "$total" "$(plural "$total")"
        printf "     gmail: %d  iMessage: %d  slack: %d\n" "$email" "$imsg" "$slack"
        echo ""
        printf "     \033[2mlast checked %s\033[0m\n" "$(date '+%H:%M:%S')"
        echo ""
        printf "     \033[32m↵  open ibx\033[0m\n"
    else
        bash "$TERM_COLOR" black
        printf "  \033[2m○  inbox zero\033[0m\n"
        printf "     \033[2mgmail: %d  iMessage: %d  slack: %d\033[0m\n" "$email" "$imsg" "$slack"
        echo ""
        printf "     \033[2mlast checked %s · next in ${POLL}s · ↵ to check now\033[0m\n" "$(date '+%H:%M:%S')"
    fi

    echo "$total"
}

# ── Main loop ─────────────────────────────────────────────────────────────────

# Trap Ctrl+C cleanly — restore black on exit
trap 'bash "$TERM_COLOR" black; echo ""; exit 0' INT TERM

echo "ibx monitor starting..."
sleep 0.5

while true; do
    total=$(check_and_display)

    # Wait up to POLL seconds for Enter; if pressed, handle it
    if read -r -t "$POLL" _; then
        if [ "$total" -gt 0 ]; then
            echo ""
            echo "  Opening ibx..."
            sleep 0.3
            bash "$IBX_SH"
            # After ibx exits, re-check immediately (don't sleep)
        fi
        # If inbox zero and Enter pressed, just re-check immediately
    fi
    # Loop: re-check after either timeout or Enter
done
