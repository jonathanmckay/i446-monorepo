#!/bin/bash
# git-autopush.sh — auto-commit and push any changes in i446-monorepo
# Runs every 10 minutes via cron.

REPO_DIR="${1:-$HOME/i446-monorepo}"
PREFIX="${2:-auto}"
TS=$(date '+%Y-%m-%d %H:%M:%S')

cd "$REPO_DIR" || { echo "[$TS] ERROR: cd $REPO_DIR failed"; exit 1; }

# Stage all changes
git add -A

BRANCH=$(git rev-parse --abbrev-ref HEAD)
# If nothing to commit, log and exit -- UNLESS earlier commits never reached
# origin. A failed pull/push leaves the branch ahead of origin; this early
# exit used to skip the sync step entirely on every later "no changes" run,
# so ix sat 33 commits ahead for a day printing "no changes" (2026-10-02).
if git diff --cached --quiet; then
    UNPUSHED=$(git rev-list --count "origin/$BRANCH..HEAD" 2>/dev/null || echo 0)
    if [ "${UNPUSHED:-0}" = "0" ]; then
        echo "[$TS] no changes"
        exit 0
    fi
    echo "[$TS] no changes, but $UNPUSHED unpushed commit(s) -- syncing"
else
    CHANGED=$(git diff --cached --stat | tail -1)
    if ! git commit -m "$PREFIX: $(date '+%Y-%m-%d %H:%M')" -q; then
        echo "[$TS] ERROR: commit failed (pre-commit hook rejected it?) — changes remain staged"
        exit 1
    fi
    echo "[$TS] committed: $CHANGED"
fi

# Push the CURRENT branch, not a hardcoded main. This lets a clone sit on a
# `wip` branch so the every-10-min auto-snapshots accumulate there and keep
# `main` clean for deliberate, tested commits. Release with release-to-main.sh.
# Sync with origin in an ISOLATED WORKTREE (2026-10-03). This repo lives
# inside a Syncthing-shared folder. The old in-place `git pull --rebase`
# checked origin's tree out into the live working tree for the seconds the
# replay took, and on a conflict aborted back; Syncthing's filewatcher saw
# both flips as edits and propagated the stale content to the other machine,
# where it overwrote (or conflicted with) work being saved at that moment
# (confirmed 2026-10-03 07:50: a patch to tools/did/dtd.sh written on
# Straylight 3s after Ix's cron fired came back as a sync-conflict file).
# Ix's vault-autopush.sh already rebases in a /tmp worktree for exactly this
# reason; this script now does the same, and moves the branch with
# `reset --keep`, which updates only the files origin changed and REFUSES
# (rather than discards) if any of those carry fresh uncommitted edits.
if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
    if ! git fetch -q origin "$BRANCH" 2>&1; then
        echo "[$TS] WARNING: fetch failed, skipping push"
        exit 1
    fi
    if ! git merge-base --is-ancestor "origin/$BRANCH" HEAD; then
        WT="/tmp/git-autopush-wt-$(basename "$REPO_DIR")"
        LOCAL_HEAD=$(git rev-parse HEAD)
        git worktree remove --force "$WT" >/dev/null 2>&1; rm -rf "$WT"; git worktree prune >/dev/null 2>&1
        if ! git worktree add --detach "$WT" "$LOCAL_HEAD" -q 2>&1; then
            echo "[$TS] WARNING: worktree add failed, skipping push"
            exit 1
        fi
        if (cd "$WT" && git -c submodule.recurse=false rebase "origin/$BRANCH" -q >/dev/null 2>&1); then
            echo "[$TS] rebased onto origin/$BRANCH (isolated worktree)"
        else
            (cd "$WT" && git rebase --abort >/dev/null 2>&1; git checkout -q --detach "$LOCAL_HEAD" >/dev/null 2>&1)
            # Reconcile fallback (opt-in via AUTOPUSH_RECONCILE=1 in the cron line):
            # on the designated autopusher the working tree is the Syncthing-
            # replicated truth, and a rebase replays every 10-min snapshot one by
            # one, so it conflicts on INTERMEDIATE states even when the final trees
            # agree. A single merge of the tips, local content winning on any
            # conflicting hunk, lands exactly the tree every machine already shows.
            if [ "${AUTOPUSH_RECONCILE:-0}" = "1" ] && (cd "$WT" && git -c submodule.recurse=false merge -X ours --no-edit -q \
                    -m "$PREFIX: reconcile with origin/$BRANCH $(date '+%Y-%m-%d %H:%M')" "origin/$BRANCH" >/dev/null 2>&1); then
                echo "[$TS] reconciled: rebase conflicted; merged origin/$BRANCH with local content winning"
            else
                (cd "$WT" && git merge --abort >/dev/null 2>&1)
                git worktree remove --force "$WT" >/dev/null 2>&1
                echo "[$TS] WARNING: rebase onto origin/$BRANCH failed in isolated worktree (tree untouched), skipping push. See git status."
                exit 1
            fi
        fi
        NEW_TIP=$(cd "$WT" && git rev-parse HEAD)
        git worktree remove --force "$WT" >/dev/null 2>&1
        if ! git reset -q --keep "$NEW_TIP" 2>&1; then
            echo "[$TS] WARNING: reset --keep refused (uncommitted edits on files origin changed); skipping push, retrying next run"
            exit 1
        fi
    fi
fi
git push -u origin "$BRANCH" -q 2>&1 || echo "[$TS] WARNING: push failed"
echo "[$TS] pushed → $BRANCH"
