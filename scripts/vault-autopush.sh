#!/bin/bash
# vault-autopush.sh — auto-commit, rebase, and push changes in ~/vault.
# Runs every 10 minutes via launchd (com.jm.vault-autopush).
#
# Order: commit local (in ~/vault) -> attempt rebase in an ISOLATED worktree
# (never in ~/vault itself) -> only touch ~/vault's working tree with a
# single atomic reset once the rebase is confirmed clean -> push.
#
# WHY THE WORKTREE (added 2026-09-07): ~/vault is bidirectionally synced to
# Straylight via Syncthing, which watches the filesystem directly and knows
# nothing about git. A `git pull --rebase` run IN ~/vault replays commits
# one at a time, writing intermediate (and, on conflict, broken/conflicted)
# tree states to disk before either landing cleanly or aborting. Syncthing
# can catch and ship one of those transient states to Straylight before the
# abort restores the real content -- silently overwriting live files there
# with garbage from mid-replay. Confirmed 2026-09-07: this had been stuck
# failing the same conflict every 10 min since 2026-08-23, and two unrelated
# vault files came back on Straylight containing z_ibx/new-notes.md's
# content mid-session — the same mechanism documented for i446-monorepo's
# sibling script (git-autopush.sh) after its 2026-08-16 build-order.md
# incident. Rebasing in a worktree outside ~/vault means Syncthing never
# sees the intermediate states at all; ~/vault only ever gets a single clean
# jump to a known-good tip, or is left completely untouched on failure.

REPO_DIR="$HOME/vault"
WORKTREE_DIR="/tmp/vault-autopush-wt"
ALERT_SCRIPT="$HOME/i446-monorepo/scripts/vault-autopush-alert.py"
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

if ! git fetch origin main -q 2>/tmp/vault-autopush-fetch.err; then
    echo "[$TS] ERROR: fetch failed: $(head -1 /tmp/vault-autopush-fetch.err)"
    exit 1
fi

LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_HEAD=$(git rev-parse origin/main)

if [ "$LOCAL_HEAD" = "$REMOTE_HEAD" ]; then
    echo "[$TS] up to date, nothing to push"
    python3 "$ALERT_SCRIPT" --clear
    exit 0
fi

# Fast path: local already contains remote's history -> plain push, no
# rebase (and so no isolated worktree) needed at all.
if git merge-base --is-ancestor "$REMOTE_HEAD" "$LOCAL_HEAD"; then
    if git push -q origin main 2>/tmp/vault-autopush-push.err; then
        echo "[$TS] pushed (fast-forward, no rebase needed)"
        python3 "$ALERT_SCRIPT" --clear
        exit 0
    else
        echo "[$TS] ERROR: push failed: $(head -1 /tmp/vault-autopush-push.err)"
        python3 "$ALERT_SCRIPT" --fail "push failed: $(head -1 /tmp/vault-autopush-push.err)"
        exit 1
    fi
fi

# Otherwise: histories diverged. Attempt the rebase in an ISOLATED worktree
# — $REPO_DIR's working tree is never written to during this attempt.
git worktree remove --force "$WORKTREE_DIR" >/dev/null 2>&1
rm -rf "$WORKTREE_DIR"
if ! git worktree add --detach "$WORKTREE_DIR" "$LOCAL_HEAD" -q 2>/tmp/vault-autopush-worktree.err; then
    echo "[$TS] ERROR: worktree add failed: $(head -1 /tmp/vault-autopush-worktree.err)"
    python3 "$ALERT_SCRIPT" --fail "worktree add failed: $(head -1 /tmp/vault-autopush-worktree.err)"
    exit 1
fi

(cd "$WORKTREE_DIR" && git -c submodule.recurse=false rebase origin/main -q) \
    2>/tmp/vault-autopush-rebase.err
REBASE_RC=$?

if [ $REBASE_RC -ne 0 ]; then
    (cd "$WORKTREE_DIR" && git rebase --abort 2>/dev/null)
    git worktree remove --force "$WORKTREE_DIR" 2>/dev/null
    echo "[$TS] WARN: rebase failed in isolated worktree (~/vault untouched)."
    echo "[$TS] See /tmp/vault-autopush-rebase.err:"
    head -8 /tmp/vault-autopush-rebase.err
    python3 "$ALERT_SCRIPT" --fail "rebase conflict: $(head -3 /tmp/vault-autopush-rebase.err | tr '\n' ' ')"
    exit 1
fi

NEW_TIP=$(cd "$WORKTREE_DIR" && git rev-parse HEAD)
git worktree remove --force "$WORKTREE_DIR"

# Single atomic jump of the live tree to the new, already-verified-clean tip.
git reset --hard "$NEW_TIP" -q

if git push -q origin main 2>/tmp/vault-autopush-push.err; then
    echo "[$TS] pushed (rebased via isolated worktree)"
    python3 "$ALERT_SCRIPT" --clear
else
    echo "[$TS] ERROR: push failed: $(head -1 /tmp/vault-autopush-push.err)"
    python3 "$ALERT_SCRIPT" --fail "push failed after clean rebase: $(head -1 /tmp/vault-autopush-push.err)"
    exit 1
fi
