#!/bin/bash
# upload-claude-stats.sh — upload Claude Code stats to m5x2 AI dashboard
# One-time or manual upload. Run whenever you want to refresh your stats.
# Usage: bash upload-claude-stats.sh

set -e

USER_ID="matt"
REPO_DIR="$HOME/m5x2-ai-stats"
STATS_FILE="$HOME/.claude/stats-cache.json"

echo "m5x2 AI stats uploader — user: $USER_ID"
echo ""

# 1. Check for stats file
if [ ! -f "$STATS_FILE" ]; then
  echo "Error: Claude stats file not found at $STATS_FILE"
  echo "Make sure Claude Code has been run at least once."
  exit 1
fi

# 2. Install gh CLI if missing
if ! command -v gh &>/dev/null; then
  echo "→ Installing GitHub CLI..."
  if command -v brew &>/dev/null; then
    brew install gh
  else
    echo "Error: Homebrew not found. Install it first: https://brew.sh"
    exit 1
  fi
fi

# 3. Authenticate with GitHub if needed
if ! gh auth status &>/dev/null; then
  echo "→ Logging in to GitHub (browser will open)..."
  gh auth login --web -h github.com
fi
echo "→ GitHub auth OK"

# 4. Clone or update repo
if [ -d "$REPO_DIR/.git" ]; then
  echo "→ Updating local repo..."
  git -C "$REPO_DIR" pull --rebase origin main -q 2>/dev/null || true
else
  echo "→ Cloning ai-stats repo..."
  gh repo clone m5x2/ai-stats "$REPO_DIR"
fi

# 5. Copy and push stats
mkdir -p "$REPO_DIR/$USER_ID"
cp "$STATS_FILE" "$REPO_DIR/$USER_ID/stats-cache.json"

cd "$REPO_DIR"
git add "$USER_ID/stats-cache.json"

if git diff --cached --quiet; then
  echo "→ Stats unchanged, nothing to push."
else
  git commit -m "stats: $USER_ID $(date '+%Y-%m-%d %H:%M')" -q
  git pull --rebase origin main -q 2>/dev/null || true
  git push origin main -q
  echo "→ Stats uploaded successfully."
fi

echo ""
echo "Done. Your stats are now visible on the m5x2 AI dashboard."
