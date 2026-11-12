#!/usr/bin/env bash
# Publish the m5x2 goals dashboard (the Flask app on ix:5559) to the m5x2 dashboards
# site as https://m5x2.github.io/appfolio-dashboards-site/goals.html, listed under
# Ad hoc by m5x2/appfolio-dashboards' templates/index.html (JM, 2026-11-11; it was
# the unlisted goals-staging.html on 2026-11-10, removed here). Encrypted with the
# shared staticrypt password AND salt (.staticrypt.json, copied from the CI repo) so
# one site login opens this page too; --remember 1 plus the pre-ticked box as in the
# site CI. Mirrors tools/m5x2-occupancy-dashboard/refresh.sh; the push retry mirrors
# leasing-goals.yml, since the site CI may push at the same moment.
# Cron on ix: 17 */2 * * * (offset from build.yml at :00 and leasing-goals at :30).
#
# Usage: publish.sh [--no-status]   (--no-status skips the live AppFolio pull)
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
PAGE=goals.html
OLD=goals-staging.html
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
npx --yes staticrypt@latest .publish-src.html -p "$(cat "$PW_FILE")" -d .publish --short --remember 1 >/dev/null
sed -i '' 's|name="remember" />|name="remember" checked />|' .publish/.publish-src.html
git -C "$SITE" pull -q --rebase
cp .publish/.publish-src.html "$SITE/$PAGE"
rm -rf .publish .publish-src.html
if [ -e "$SITE/$OLD" ]; then git -C "$SITE" rm -q "$OLD"; fi
if [ -n "$(git -C "$SITE" status --porcelain "$PAGE" "$OLD")" ]; then
  git -C "$SITE" add "$PAGE"
  git -C "$SITE" commit -q -m "goals dashboard update $(date '+%Y-%m-%d %H:%M')"
  for i in 1 2 3 4 5; do
    if git -C "$SITE" push -q; then echo "published $PAGE"; break; fi
    if [ "$i" = 5 ]; then echo "push failed after 5 tries" >&2; exit 1; fi
    sleep $((i * 5)); git -C "$SITE" pull -q --rebase
  done
else
  echo "no site changes"
fi
echo "── done ──"
