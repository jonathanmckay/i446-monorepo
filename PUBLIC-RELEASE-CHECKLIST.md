# i446-monorepo Public Release Checklist

## Pre-Release Security Audit ✅

- [x] Scanned git history for secrets
- [x] Found hardcoded OAuth credentials in deleted files
- [x] Verified .env files are gitignored
- [x] Created comprehensive .gitignore
- [x] Created cleanup script

## Critical Security Steps 🚨

### 1. Revoke Compromised Credentials (DO THIS FIRST!)

**Microsoft OAuth Secret Found:**
- Secret: `EF88Q~2lqQaxc9QmhNcxKSIbXBNlsrYeZS4ObaP8`
- Location: Deleted files still in git history (from retired x954-g245.1 project)

**Action Required:**
- [ ] Log into Azure Portal (portal.azure.com)
- [ ] Go to App registrations → Find your old OneDrive/x954 app
- [ ] Go to Certificates & secrets
- [ ] Delete the old secret (or delete the entire app if no longer used)

**Note:** x954-g245.1 has been retired (replaced by excel-mcp). The OAuth app is no longer needed and can be fully deleted from Azure.

### 2. Clean Git History

**Run the cleanup script:**
```bash
cd ~/i446-monorepo
./CLEANUP-FOR-PUBLIC.sh
```

This will:
- Create a backup
- Remove `x954-g245.1/debug_onedrive.py` from all history
- Remove `x954-g245.1/setup_oauth.py` from all history
- Verify the secrets are gone

**Checklist:**
- [ ] Run cleanup script successfully
- [ ] Verify backup was created
- [ ] Confirm secrets removed from history

### 3. Update Documentation

**Add README files:**
- [ ] Create main README.md with project overview
- [ ] Document setup requirements
- [ ] Add environment variable documentation
- [ ] Create individual READMEs for projects missing them

**Projects needing READMEs:**
- [ ] appfolio-attrition/README.md
- [ ] classify-conversations/README.md
- [ ] m5x2-kb/README.md (currently empty)
- [ ] scripts/README.md

### 4. Review Current Files

**Check for any overlooked sensitive data:**
- [ ] Review all .env.example files (if any)
- [ ] Check for hardcoded URLs/domains that should be configurable
- [ ] Review commit messages for sensitive info
- [ ] Check for personal data in code comments

### 5. Push to GitHub

**Create new remote (if needed):**
```bash
cd ~/i446-monorepo
git remote add origin git@github.com:jonathanmckay/i446-monorepo.git
```

**Force push rewritten history:**
```bash
git push origin --force --all
git push origin --force --tags
```

**Checklist:**
- [ ] Repository created on GitHub (private first for testing)
- [ ] Force pushed rewritten history
- [ ] Verified secrets not visible in GitHub UI
- [ ] Checked GitHub "Security" tab for detected secrets

### 6. Make Public

**On GitHub:**
- [ ] Go to Settings → Danger Zone → Change repository visibility
- [ ] Change from Private to Public
- [ ] Confirm the change

### 7. Verify Public Release

**Final checks:**
- [ ] Clone the repo fresh to verify it works: `git clone git@github.com:jonathanmckay/i446-monorepo.git /tmp/test-clone`
- [ ] Check GitHub contribution graph shows commits
- [ ] Verify your dashboard picks up the commits
- [ ] Confirm no secrets visible in web interface
- [ ] Check that .gitignore is working (try adding a .env file)

### 8. Post-Release

**Notify anyone with old clones:**
- [ ] If any collaborators exist, tell them to delete and re-clone
- [ ] Old clones will still have the compromised secrets in history

**Update references:**
- [ ] Update any symlinks in `~/vault/i447/i446/` if needed
- [ ] Update documentation that references the repo

## Environment Variables to Document

Create a `.env.example` file showing what's needed:

```bash
# appfolio-attrition
APPFOLIO_CLIENT_ID=your_appfolio_client_id
APPFOLIO_CLIENT_SECRET=your_appfolio_secret
APPFOLIO_BASE_URL=https://yourvhost.appfolio.com

# Toggl (if used)
TOGGL_API_TOKEN=your_toggl_token

# Todoist (if used)
TODOIST_API_KEY=your_todoist_key
```

## Notes

- Backup location: `~/i446-monorepo-backup-YYYYMMDD-HHMMSS/`
- Original history preserved in backup
- Force push rewrites history for all collaborators
- Old credentials must be revoked before going public
