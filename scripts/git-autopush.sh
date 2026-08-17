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
if ! git commit -m "$PREFIX: $(date '+%Y-%m-%d %H:%M')" -q; then
    echo "[$TS] ERROR: commit failed (pre-commit hook rejected it?) — changes remain staged"
    exit 1
fi
echo "[$TS] committed: $CHANGED"

# Push the CURRENT branch, not a hardcoded main. This lets a clone sit on a
# `wip` branch so the every-10-min auto-snapshots accumulate there and keep
# `main` clean for deliberate, tested commits. Release with release-to-main.sh.
BRANCH=$(git rev-parse --abbrev-ref HEAD)
# Only rebase if the remote branch already exists (first push creates it).
if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
    if ! git pull --rebase origin "$BRANCH" -q 2>&1; then
        # A failed rebase leaves the working tree mid-replay -- some files can
        # show an OLDER commit's content, not this run's true pre-rebase
        # state. Left uncleaned (as this branch used to), that stale content
        # sits on disk where Syncthing's filewatcher (this repo's ~/vault
        # folder is bidirectionally synced with Ix, unaware of git entirely)
        # picks it up as "the file changed" and propagates it to Ix,
        # silently overwriting live, correctly-written data there -- bypassing
        # every application-level lock (confirmed 2026-08-16: a build-order.md
        # ritual stamp made on Ix vanished this way after landing here mid a
        # failed rebase). Ix's own vault-autopush.sh already aborts on
        # failure; this script must too, and skip the push below entirely
        # (pushing a mid-rebase HEAD would fail or push garbage anyway).
        git rebase --abort 2>/dev/null
        echo "[$TS] WARNING: pull --rebase failed, skipping push. See git status."
        exit 1
    fi
fi
git push -u origin "$BRANCH" -q 2>&1 || echo "[$TS] WARNING: push failed"
echo "[$TS] pushed → $BRANCH"
