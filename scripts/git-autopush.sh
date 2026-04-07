#!/bin/bash
# git-autopush.sh — auto-commit and push any changes in i446-monorepo
# Runs every 10 minutes via cron.

REPO_DIR="$HOME/i446-monorepo"

cd "$REPO_DIR" || exit 1

# Stage all changes
git add -A

# If nothing to commit, exit silently
git diff --cached --quiet && exit 0

git commit -m "auto: $(date '+%Y-%m-%d %H:%M')" -q
git pull --rebase origin main -q 2>/dev/null || true
git push origin main -q 2>/dev/null || true
