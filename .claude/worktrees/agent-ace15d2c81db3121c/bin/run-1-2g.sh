#!/usr/bin/env bash
# Daily /1-2g audit — invokes the Claude skill to fix missing (N), [N], and labels on Todoist tasks
# Installed by Dream v5 card 11 (2026-05-24)
LOG=~/i446-monorepo/.run-1-2g.log
echo "=== $(date) ===" >> "$LOG"
/Users/mckay/.claude-cli/CurrentVersion/claude --print --dangerously-skip-permissions "/1-2g" >> "$LOG" 2>&1
