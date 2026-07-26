#!/usr/bin/env bash
# Refresh the m5x2 occupancy dashboard: pull fresh AppFolio data, rebuild index.html.
# --daily 7 re-pulls the last 7 days every run, so any missed day self-heals
# (no more 6/15→6/22 gaps). Idempotent per date. Run by cron daily; also safe
# to run by hand: `bash refresh.sh`.
set -euo pipefail
cd "$(dirname "$0")"
# cron on Ix has a bare PATH; npx/node live in homebrew
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
echo "── refresh $(date '+%Y-%m-%d %H:%M:%S') ──"
python3 fetch.py --daily 7
python3 build.py

# ── Publish to https://m5x2.github.io/appfolio-dashboards-site/occupancy.html ──
# (2026-07-26, per JM.) Mirrors the appfolio-dashboards CI model: staticrypt
# with the SAME shared password as the other dashboards (from a local file the
# repo never sees), copy into the site repo clone, push. The site CI is
# additive (cp output/*.html), so occupancy.html is never clobbered by it.
PW_FILE="$HOME/.config/m5x2/staticrypt-password"
SITE="$HOME/appfolio-dashboards-site"
if [[ -s "$PW_FILE" && -d "$SITE/.git" ]]; then
  echo "── publish ──"
  npx --yes staticrypt@latest index.html -p "$(cat "$PW_FILE")" -d .publish --short
  git -C "$SITE" pull -q --rebase
  cp .publish/index.html "$SITE/occupancy.html"
  rm -rf .publish
  if ! git -C "$SITE" diff --quiet -- occupancy.html || [ -n "$(git -C "$SITE" status --porcelain occupancy.html)" ]; then
    git -C "$SITE" add occupancy.html
    git -C "$SITE" commit -q -m "occupancy dashboard update $(date '+%Y-%m-%d')"
    git -C "$SITE" push -q
    echo "published occupancy.html"
  else
    echo "no site changes"
  fi
else
  echo "SKIP publish: missing $PW_FILE or site clone at $SITE"
fi
echo "── done ──"
