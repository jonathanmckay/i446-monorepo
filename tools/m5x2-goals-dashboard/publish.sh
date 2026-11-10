#!/usr/bin/env bash
# Publish the goals dashboard as an UNLISTED staging page on the m5x2 dashboards
# site (https://m5x2.github.io/appfolio-dashboards-site/goals-staging.html),
# encrypted with the shared staticrypt password like every page there (the site
# repo is public). Mirrors tools/m5x2-occupancy-dashboard/refresh.sh. Promotion
# to a listed page = moving the render into m5x2/appfolio-dashboards' build.py.
#
# Usage: publish.sh [--no-status]   (--no-status skips the live AppFolio pull)
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
echo "── goals publish $(date '+%Y-%m-%d %H:%M:%S') ──"
if [[ "${1:-}" != "--no-status" ]]; then
  python3 status.py --write >/dev/null
fi
python3 dashboard.py --static .publish-src.html
PW_FILE="$HOME/.config/m5x2/staticrypt-password"
SITE="$HOME/code/appfolio-dashboards-site"
if [[ ! -s "$PW_FILE" || ! -d "$SITE/.git" ]]; then
  echo "SKIP publish: missing $PW_FILE or site clone at $SITE"; exit 0
fi
rm -rf .publish && mkdir .publish
npx --yes staticrypt@latest .publish-src.html -p "$(cat "$PW_FILE")" -d .publish --short >/dev/null
git -C "$SITE" pull -q --rebase
cp .publish/.publish-src.html "$SITE/goals-staging.html"
rm -rf .publish .publish-src.html
if [ -n "$(git -C "$SITE" status --porcelain goals-staging.html)" ]; then
  git -C "$SITE" add goals-staging.html
  git -C "$SITE" commit -q -m "goals staging dashboard update $(date '+%Y-%m-%d %H:%M')"
  git -C "$SITE" push -q
  echo "published goals-staging.html"
else
  echo "no site changes"
fi
echo "── done ──"
