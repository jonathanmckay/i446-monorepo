#!/bin/bash
# vault-autopush.sh — auto-commit, rebase, and push changes in ~/vault.
# Runs every 10 minutes via launchd (com.jm.vault-autopush).
#
# Order: commit local → pull --rebase (skip submodule recursion) → push.
# If rebase hits conflicts, abort and leave the local commit in place; the
# next cycle will retry. Manual resolution needed if conflicts persist.

REPO_DIR="$HOME/vault"
TS=$(date '+%Y-%m-%d %H:%M:%S')

cd "$REPO_DIR" || { echo "[$TS] ERROR: cd $REPO_DIR failed"; exit 1; }

git add -A

if ! git diff --cached --quiet; then
    CHANGED=$(git diff --cached --stat | tail -1)
    git commit -m "vault backup: $(date '+%Y-%m-%d %H:%M:%S')" -q || {
        echo "[$TS] ERROR: commit failed"
        exit 1
    }
    echo "[$TS] committed: $CHANGED"
fi

# Rebase local on top of remote. Don't recurse into submodule (submodule
# fetches can fail for unrelated reasons and shouldn't block the backup).
if ! git -c submodule.recurse=false pull --rebase origin main 2>/tmp/vault-autopush-rebase.err; then
    # Rebase failed — likely conflicts. Abort and bail.
    git rebase --abort 2>/dev/null
    echo "[$TS] WARN: pull --rebase failed, skipping push. See /tmp/vault-autopush-rebase.err"
    head -3 /tmp/vault-autopush-rebase.err
    exit 1
fi

if git push -q origin main 2>/tmp/vault-autopush-push.err; then
    echo "[$TS] pushed"
else
    echo "[$TS] ERROR: push failed: $(head -1 /tmp/vault-autopush-push.err)"
    exit 1
fi
