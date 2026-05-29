#!/bin/bash
# git-autopush.sh — auto-commit and push any changes in i446-monorepo
# Runs every 10 minutes via cron.

REPO_DIR="${1:-$HOME/i446-monorepo}"
PREFIX="${2:-auto}"
TS=$(date '+%Y-%m-%d %H:%M:%S')

cd "$REPO_DIR" || { echo "[$TS] ERROR: cd $REPO_DIR failed"; exit 1; }

# Stage all changes
git add -A

# If nothing to commit, log and exit
if git diff --cached --quiet; then
    echo "[$TS] no changes"
    exit 0
fi

CHANGED=$(git diff --cached --stat | tail -1)
git commit -m "$PREFIX: $(date '+%Y-%m-%d %H:%M')" -q
echo "[$TS] committed: $CHANGED"

git pull --rebase origin main -q 2>&1 || echo "[$TS] WARNING: pull failed"
git push origin main -q 2>&1 || echo "[$TS] WARNING: push failed"
echo "[$TS] pushed"
