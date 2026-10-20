#!/bin/bash
# setup-ai-tracking.sh — one-time setup for m5x2 AI usage tracking
# Usage: bash setup-ai-tracking.sh <user_id>
# Example: bash setup-ai-tracking.sh lx

set -e

USER_ID="${1:-}"
if [ -z "$USER_ID" ]; then
  echo "Usage: $0 <user_id>  (e.g., lx, ian, jm)"
  exit 1
fi

REPO_DIR="$HOME/m5x2-ai-stats"
SYNC_SCRIPT="$HOME/.claude/sync-stats.sh"

echo "Setting up AI tracking for user: $USER_ID"
echo ""

# 1. Install gh CLI if missing
if ! command -v gh &>/dev/null; then
  echo "→ Installing GitHub CLI..."
  if command -v brew &>/dev/null; then
    brew install gh
  else
    echo "Error: Homebrew not found. Install it first: https://brew.sh"
    exit 1
  fi
fi

# 2. Authenticate with GitHub if needed
if ! gh auth status &>/dev/null; then
  echo "→ Logging in to GitHub (browser will open)..."
  gh auth login --web -h github.com
fi
echo "→ GitHub auth OK"

# 3. Clone or update repo
if [ -d "$REPO_DIR/.git" ]; then
  echo "→ Repo already cloned, pulling latest..."
  git -C "$REPO_DIR" pull --rebase origin main -q 2>/dev/null || true
else
  echo "→ Cloning ai-stats repo..."
  gh repo clone m5x2/ai-stats "$REPO_DIR"
fi

# 4. Install sync script
mkdir -p "$HOME/.claude"
cat > "$SYNC_SCRIPT" << 'SYNCEOF'
#!/bin/bash
# sync-stats.sh — auto-syncs Claude Code stats to m5x2/ai-stats repo
REPO_DIR="$HOME/m5x2-ai-stats"
USER_ID="${M5X2_USER_ID:-}"
STATS_FILE="$HOME/.claude/stats-cache.json"

[ -z "$USER_ID" ] && exit 0
[ -f "$STATS_FILE" ] || exit 0
[ -d "$REPO_DIR/.git" ] || exit 0

mkdir -p "$REPO_DIR/$USER_ID"
cp "$STATS_FILE" "$REPO_DIR/$USER_ID/stats-cache.json"

cd "$REPO_DIR"
git add "$USER_ID/stats-cache.json"
git diff --cached --quiet && exit 0

git commit -m "stats: $USER_ID $(date '+%Y-%m-%d %H:%M')" -q
git pull --rebase origin main -q 2>/dev/null || true
git push origin main -q 2>/dev/null || true
SYNCEOF
chmod +x "$SYNC_SCRIPT"
echo "→ Sync script installed"

# 5. Add M5X2_USER_ID to shell profile
if [ -f "$HOME/.zshrc" ]; then
  PROFILE="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
  PROFILE="$HOME/.bashrc"
else
  PROFILE="$HOME/.profile"
fi

if grep -q "M5X2_USER_ID" "$PROFILE" 2>/dev/null; then
  echo "→ M5X2_USER_ID already set in $PROFILE"
else
  echo "export M5X2_USER_ID=$USER_ID" >> "$PROFILE"
  echo "→ Added M5X2_USER_ID=$USER_ID to $PROFILE"
fi
export M5X2_USER_ID="$USER_ID"

# 6. Add Claude Code Stop hook
python3 - "$SYNC_SCRIPT" << 'PYEOF'
import json, os, sys

settings_path = os.path.expanduser("~/.claude/settings.json")
sync_script = sys.argv[1]

try:
    with open(settings_path) as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    settings = {}

hooks = settings.setdefault("hooks", {})
stop_hooks = hooks.setdefault("Stop", [])

already = any(
    h.get("type") == "command" and "sync-stats" in h.get("command", "")
    for group in stop_hooks
    for h in group.get("hooks", [])
)

if not already:
    stop_hooks.append({
        "matcher": "",
        "hooks": [{"type": "command", "command": sync_script}]
    })
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
    print("→ Claude Code Stop hook installed")
else:
    print("→ Hook already installed, skipping")
PYEOF

echo ""
echo "Done! Stats will sync automatically after each Claude Code session."
echo "  User: $USER_ID"
