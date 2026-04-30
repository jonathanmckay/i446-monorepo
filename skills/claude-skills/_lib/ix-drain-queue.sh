#!/usr/bin/env bash
# ix-drain-queue.sh — Replay queued ix writes from ix-write-queue.jsonl
# Run when ix is back online to flush pending Neon writes.
#
# Usage:
#   ix-drain-queue.sh           # replay all, remove on success
#   ix-drain-queue.sh --dry-run # show what would run without executing

set -u
readonly QUEUE="${HOME}/.claude/ix-write-queue.jsonl"
readonly DRY_RUN="${1:-}"

if [ ! -f "$QUEUE" ] || [ ! -s "$QUEUE" ]; then
    echo "No queued writes."
    exit 0
fi

total=$(wc -l < "$QUEUE" | tr -d ' ')
echo "Draining $total queued write(s)..."

success=0
failed=0
remaining_file=$(mktemp)

while IFS= read -r line; do
    script=$(echo "$line" | python3 -c "import json,sys; print(json.load(sys.stdin)['script'])" 2>/dev/null)
    ts=$(echo "$line" | python3 -c "import json,sys; print(json.load(sys.stdin)['ts'])" 2>/dev/null)

    if [ -z "$script" ]; then
        echo "  SKIP: malformed entry"
        echo "$line" >> "$remaining_file"
        ((failed++))
        continue
    fi

    echo "  [$ts] replaying..."
    if [ "$DRY_RUN" = "--dry-run" ]; then
        echo "    (dry run) would execute: ${script:0:80}..."
        ((success++))
        continue
    fi

    echo "$script" | ssh -o ConnectTimeout=3 -o BatchMode=yes ix osascript - 2>/dev/null
    rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "    OK"
        ((success++))
    elif [ "$rc" -eq 255 ]; then
        echo "    FAILED: ix still unreachable. Stopping drain."
        echo "$line" >> "$remaining_file"
        # Keep remaining lines
        cat >> "$remaining_file"
        break
    else
        echo "    FAILED: AppleScript error (rc=$rc). Discarding."
        ((success++))  # don't retry broken scripts
    fi
done < "$QUEUE"

if [ "$DRY_RUN" != "--dry-run" ]; then
    if [ -s "$remaining_file" ]; then
        mv "$remaining_file" "$QUEUE"
        remaining=$(wc -l < "$QUEUE" | tr -d ' ')
        echo "Done: $success replayed, $remaining still queued."
    else
        rm -f "$QUEUE" "$remaining_file"
        echo "Done: $success replayed, queue empty."
    fi
else
    rm -f "$remaining_file"
    echo "Dry run complete: $success entries would replay."
fi
