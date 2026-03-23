#!/bin/bash
# Cleanup script to prepare i446-monorepo for public release
# This script removes sensitive files from git history

set -e

echo "🔍 i446-monorepo Public Release Cleanup"
echo "========================================"
echo ""
echo "⚠️  WARNING: This will rewrite git history!"
echo "⚠️  Make sure you have a backup before proceeding."
echo ""

# Check if we're in the right directory
if [ ! -d ".git" ]; then
    echo "❌ Error: Not in a git repository"
    exit 1
fi

if [ "$(basename $(pwd))" != "i446-monorepo" ]; then
    echo "⚠️  Warning: Current directory is not 'i446-monorepo'"
    echo "Current: $(pwd)"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Step 1: Check for git-filter-repo
echo "📦 Step 1: Checking for git-filter-repo..."
if ! command -v git-filter-repo &> /dev/null; then
    echo "   Installing git-filter-repo via Homebrew..."
    brew install git-filter-repo
else
    echo "   ✅ git-filter-repo already installed"
fi
echo ""

# Step 2: Create backup
echo "💾 Step 2: Creating backup..."
BACKUP_DIR="${HOME}/i446-monorepo-backup-$(date +%Y%m%d-%H%M%S)"
echo "   Backing up to: $BACKUP_DIR"
cp -r . "$BACKUP_DIR"
echo "   ✅ Backup created at $BACKUP_DIR"
echo ""

# Step 3: Show what will be removed
echo "🔍 Step 3: Files to be removed from history:"
echo "   - x954-g245.1/debug_onedrive.py (contains hardcoded OAuth secret)"
echo "   - x954-g245.1/setup_oauth.py (contains hardcoded OAuth secret)"
echo ""

read -p "Proceed with removing these files from git history? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ Aborted by user"
    exit 1
fi

# Step 4: Remove sensitive files from history
echo "🧹 Step 4: Removing sensitive files from git history..."
echo "   This may take a few minutes..."

git filter-repo --path x954-g245.1/debug_onedrive.py --invert-paths --force
git filter-repo --path x954-g245.1/setup_oauth.py --invert-paths --force

echo "   ✅ Sensitive files removed from history"
echo ""

# Step 5: Verify removal
echo "🔍 Step 5: Verifying removal..."
if git log --all --full-history -- x954-g245.1/debug_onedrive.py 2>&1 | grep -q "commit"; then
    echo "   ⚠️  Warning: File may still be in history"
else
    echo "   ✅ debug_onedrive.py confirmed removed"
fi

if git log --all --full-history -- x954-g245.1/setup_oauth.py 2>&1 | grep -q "commit"; then
    echo "   ⚠️  Warning: File may still be in history"
else
    echo "   ✅ setup_oauth.py confirmed removed"
fi
echo ""

# Step 6: Final check for secrets
echo "🔍 Step 6: Scanning for remaining secrets..."
if git log -p --all | grep -i "EF88Q~[REDACTED]" > /dev/null; then
    echo "   ❌ ERROR: OAuth secret still found in history!"
    echo "   Manual intervention required."
    exit 1
else
    echo "   ✅ OAuth secret confirmed removed"
fi
echo ""

# Step 7: Instructions for next steps
echo "✅ Cleanup complete!"
echo ""
echo "📋 Next steps:"
echo "1. ⚠️  CRITICAL: Revoke the old OAuth credentials in Azure/Microsoft"
echo "   - Go to Azure portal → App registrations"
echo "   - Find your app and generate new client secret"
echo "   - Update your local .env files"
echo ""
echo "2. Review the changes:"
echo "   git log --oneline | head -20"
echo ""
echo "3. Force push to rewrite remote history:"
echo "   git remote add origin git@github.com:jonathanmckay/i446-monorepo.git"
echo "   git push origin --force --all"
echo "   git push origin --force --tags"
echo ""
echo "4. Make the repository public on GitHub"
echo ""
echo "5. Tell collaborators (if any) to re-clone the repo"
echo "   (old clones will have the old history with secrets)"
echo ""
echo "⚠️  Backup saved at: $BACKUP_DIR"
