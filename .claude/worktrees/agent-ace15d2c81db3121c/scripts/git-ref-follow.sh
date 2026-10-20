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
git fetch -q origin || { /usr/bin/osascript -e "display notification \"fetch failed: $repo\" with title \"git-ref-follow\"" 2>/dev/null; exit 1; }
branch=$(git rev-parse --abbrev-ref HEAD)
up="origin/$branch"
git rev-parse -q --verify "$up" >/dev/null || exit 0
[ "$(git rev-parse HEAD)" = "$(git rev-parse "$up")" ] && exit 0
if git merge-base --is-ancestor HEAD "$up"; then
  git reset -q --mixed "$up" && echo "$(date '+%F %T') $repo: HEAD -> $(git rev-parse --short HEAD)"
else
  ahead=$(git rev-list --count "$up..HEAD"); behind=$(git rev-list --count "HEAD..$up")
  msg="$(basename "$repo") ($branch) diverged: $ahead local, $behind on origin. Needs a merge."
  echo "$(date '+%F %T') $repo: $msg"
  /usr/bin/osascript -e "display notification \"$msg\" with title \"git-ref-follow\" sound name \"Basso\"" 2>/dev/null
  exit 2
fi
