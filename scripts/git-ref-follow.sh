#!/bin/bash
# Keep a Syncthing-mirrored repo's git HEAD/index in step with origin.
# Syncthing moves the working tree; Ix's autopush moves origin; nothing moves
# this machine's HEAD, so it drifts hundreds of commits behind. This fetches and,
# when HEAD is an ancestor of origin (no local-only commits), resets HEAD+index
# to origin with --mixed. The working tree is never touched.
set -u
repo="$1"
cd "$repo" || exit 1
[ -e .git/index.lock ] && exit 0
git fetch -q origin || exit 1
branch=$(git rev-parse --abbrev-ref HEAD)
up="origin/$branch"
git rev-parse -q --verify "$up" >/dev/null || exit 0
[ "$(git rev-parse HEAD)" = "$(git rev-parse "$up")" ] && exit 0
if git merge-base --is-ancestor HEAD "$up"; then
  git reset -q --mixed "$up" && echo "$(date '+%F %T') $repo: HEAD -> $(git rev-parse --short HEAD)"
else
  echo "$(date '+%F %T') $repo: local commits not on $up; skipped"
fi
