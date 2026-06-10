#!/bin/bash
# release-to-main.sh — promote tested `wip` work onto `main`, then return to wip.
#
# Model (see z_meta/architecture.md hazard #2): the every-10-min auto-snapshots
# land on `wip`, keeping `main` for deliberate, reviewed commits. Run this when
# wip is in a good state to publish it to main.
set -e
cd "$HOME/i446-monorepo"

git rev-parse --verify wip >/dev/null 2>&1 || { echo "no wip branch; nothing to release"; exit 0; }

git checkout main
git pull --rebase origin main -q 2>&1 || echo "WARNING: pull main failed"
git merge --no-ff wip -m "release: merge wip $(date '+%Y-%m-%d %H:%M')"
git push origin main -q 2>&1 || echo "WARNING: push main failed"

# Keep wip current with the freshly-merged main so the two don't drift.
git checkout wip
git merge main -q
echo "released wip → main (now back on wip)"
