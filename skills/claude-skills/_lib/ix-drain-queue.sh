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

# Concurrent drains replay every entry twice (2026-07-24: a manual drain raced
# the watchdog's post-recovery drain and double-credited three 0分 writes).
# mkdir is the atomic lock; a stale lock (>10 min) from a killed drain is broken.
readonly LOCK="${HOME}/.claude/ix-drain-queue.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
    age=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
    if [ "$age" -lt 600 ]; then
        echo "Another drain is running (lock ${age}s old). Skipping."
        exit 0
    fi
    rmdir "$LOCK" 2>/dev/null
    mkdir "$LOCK" 2>/dev/null || { echo "Lock contention. Skipping."; exit 0; }
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

if [ ! -f "$QUEUE" ] || [ ! -s "$QUEUE" ]; then
    echo "No queued writes."
    exit 0
fi

# Claim the queue atomically: rename it so a racing drain (or a writer mid-
# append) never sees the same entries. Failures are re-queued to $QUEUE.
if [ "$DRY_RUN" = "--dry-run" ]; then
    WORK="$QUEUE"
else
    WORK="${QUEUE}.draining.$$"
    mv "$QUEUE" "$WORK" 2>/dev/null || { echo "No queued writes."; exit 0; }
fi

total=$(wc -l < "$WORK" | tr -d ' ')
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
done < "$WORK"

if [ "$DRY_RUN" != "--dry-run" ]; then
    rm -f "$WORK"
    if [ -s "$remaining_file" ]; then
        # Append (not mv): new writes may have queued to $QUEUE mid-drain.
        cat "$remaining_file" >> "$QUEUE"
        rm -f "$remaining_file"
        remaining=$(wc -l < "$QUEUE" | tr -d ' ')
        echo "Done: $success replayed, $remaining still queued."
    else
        rm -f "$remaining_file"
        echo "Done: $success replayed, queue empty."
    fi
else
    rm -f "$remaining_file"
    echo "Dry run complete: $success entries would replay."
fi
