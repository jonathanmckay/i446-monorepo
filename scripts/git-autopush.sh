#!/bin/bash
# git-autopush.sh — generic auto-commit and push for any git repo.
# Usage: git-autopush.sh <repo-path> [commit-prefix]
#
# Runs: stage all → commit → pull --rebase → push.
# If rebase conflicts, abort and retry next cycle.
#
# Cron examples:
#   */10 * * * * ~/i446-monorepo/scripts/git-autopush.sh ~/i446-monorepo "auto"
#   */10 * * * * ~/i446-monorepo/scripts/git-autopush.sh ~/vault "vault backup"
#   */30 * * * * ~/i446-monorepo/scripts/git-autopush.sh ~/vault/hcmp/o315/blog "blog"

REPO_DIR="${1:?Usage: git-autopush.sh <repo-path> [commit-prefix]}"
PREFIX="${2:-auto}"
TS=$(date '+%Y-%m-%d %H:%M:%S')
BRANCH=$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")

cd "$REPO_DIR" || { echo "[$TS] ERROR: cd $REPO_DIR failed"; exit 1; }

git add -A

if git diff --cached --quiet; then
    echo "[$TS] no changes"
    exit 0
fi

CHANGED=$(git diff --cached --stat | tail -1)
git commit -m "$PREFIX: $(date '+%Y-%m-%d %H:%M:%S')" -q || {
    echo "[$TS] ERROR: commit failed"
    exit 1
}
echo "[$TS] committed: $CHANGED"

if ! git -c submodule.recurse=false pull --rebase origin "$BRANCH" -q 2>/tmp/git-autopush-rebase.err; then
    git rebase --abort 2>/dev/null
    echo "[$TS] WARN: pull --rebase failed, skipping push. See /tmp/git-autopush-rebase.err"
    head -3 /tmp/git-autopush-rebase.err
    exit 1
fi

if git push -q origin "$BRANCH" 2>/tmp/git-autopush-push.err; then
    echo "[$TS] pushed"
else
    echo "[$TS] ERROR: push failed: $(head -1 /tmp/git-autopush-push.err)"
    exit 1
fi
