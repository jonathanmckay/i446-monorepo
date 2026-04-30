# Vault Sync — Local Network Architecture

The `~/vault/` knowledge base (Inkwell) syncs across Jonathan's two machines —
**ix** (Mac mini, workstation) and **Straylight** (laptop) — using two layers in
parallel. Each solves a different problem; together they give live Obsidian sync
AND merge semantics for concurrent Claude writes.

```
 ┌────────────┐   Syncthing LAN (sub-second, last-write-wins)   ┌─────────────┐
 │    ix      │ ◄───────────────────────────────────────────► │  Straylight │
 │  ~/vault/  │                                                 │  ~/vault/   │
 │            │                                                 │             │
 │  .git/ ────┼──► github.com:jonathanmckay/vault ◄─────────────┼──── .git/   │
 └────────────┘   git autopush (10 min, merges via rebase)       └─────────────┘
```

## Why two layers

| Problem | Syncthing | Git | Chosen layer |
|---------|-----------|-----|--------------|
| Real-time sync for Obsidian typing | ✓ sub-second | ✗ 10-min cadence | **Syncthing** |
| Concurrent writes to the same file from two agents | ✗ last-write-wins (silent data loss) | ✓ merge on rebase | **Git** |
| Machine-local state (.DS_Store, Syncthing markers) | ✓ doesn't care | ✗ would commit junk | **Syncthing** |
| Offline edits that need to survive laptop-lid-close | ✓ queues on reconnect | ✓ queues on reconnect | either |
| Visible history of changes | ✗ none | ✓ git log | **Git** |

Single-layer fails:
- **Syncthing only** (the 2026-04-23 → 2026-04-24 era): concurrent writes to
  the build order from two Claude sessions silently dropped one side's edits
  and created `*.sync-conflict-*` files. ~86 such files accumulated in a day.
- **Git only**: Obsidian on Straylight doesn't see changes from ix for up to
  10 minutes, breaking cross-machine workflows like `/did` on ix → check off
  the box on Straylight's Obsidian.

## Components

### On disk

- `~/vault/.stignore` — Syncthing ignore patterns.
  **Must include `.git` and `.git/**`** so Syncthing doesn't race git internals.
- `~/vault/.gitignore` — git ignore patterns. Excludes:
  - Obsidian workspace JSONs (per-machine view state)
  - `.DS_Store`
  - Google Drive mirror folders under `h335/m5x2/` (source of truth lives in Drive)
  - Large binary assets in `z_asts/`
  - `i447/i446/ai-transcripts/**/*.jsonl` (Claude Code session transcripts, can reach 100s of MB)
  - `i447/i446/llm-sessions.db`
  - `hcmp/o315/blog/` (submodule; re-init separately)
- `~/vault/.gitmodules` — tracks `i447/i446/i446-monorepo` submodule (this repo).
  The blog submodule at `hcmp/o315/blog` is currently gitignored on ix due to a
  busted `.git/` from the Syncthing era; needs manual re-init.

### Automation

- **`scripts/vault-autopush.sh`** — shell script:
  1. `git add -A` in `$HOME/vault`
  2. Commits as `vault backup: <timestamp>` if there's a diff
  3. `git pull --rebase origin main` with `submodule.recurse=false`
  4. If rebase fails → `git rebase --abort`, log the error, exit (next run retries)
  5. `git push origin main`
- **`~/Library/LaunchAgents/com.jm.vault-autopush.plist`** — launchd schedule:
  - `StartInterval: 600` (every 10 min)
  - `RunAtLoad: true`
  - Logs to `/tmp/vault-autopush.log` (and `.err`)

### Remote

`git@github.com:jonathanmckay/vault.git` — single `main` branch. Both machines
push/pull here. Submodule repos: `jonathanmckay/i446-monorepo`, `jonathanmckay/o315-blog-v3`.

## Setup checklist per machine

### One-time (on each Mac)

```bash
# 1. Write .stignore BEFORE git init (so Syncthing never sees .git/)
cat > ~/vault/.stignore <<'EOF'
.git
.git/**
.DS_Store
**/.DS_Store
(?d)*.sync-conflict-*
EOF

# 2. Wait a few seconds for Syncthing to pick up the ignore rule.

# 3. Init git pointing at the shared remote.
cd ~/vault
git init -b main
git remote add origin git@github.com:jonathanmckay/vault.git
git fetch origin main
git reset --mixed origin/main   # HEAD = remote, working tree untouched

# 4. Commit whatever the local filesystem has that differs from remote.
git add -A
git commit -m "initial: catch up $HOSTNAME after non-git era"
git push -u origin main

# 5. Install the launchd agent (once the plist is in place).
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jm.vault-autopush.plist

# 6. Verify.
launchctl list | grep vault-autopush
tail -f /tmp/vault-autopush.log
```

### Current status

- **ix**: set up 2026-04-24 11:29 PT. `com.jm.vault-autopush` loaded, first autopush successful.
- **Straylight**: **NOT YET SET UP.** Needs steps above. Until done, Straylight
  commits nothing and pulls nothing — it's still Syncthing-only on its side, which
  means concurrent writes will still silently lose on Straylight's side.

## Expected behavior under concurrent writes

**Scenario 1 — different sections of the same file (most common):**
You type `午` block goals in Obsidian on Straylight. Claude on ix writes to
the `0₲` section of the same build order file. Syncthing sees simultaneous
writes, picks whichever has the later mtime, pushes that to the other machine.
Loser becomes `*.sync-conflict-*`. Next autopush on each side commits whatever
it ended up with. On rebase, git auto-merges the two versions since they
touched different lines. Result: both sets of edits live in the final commit.

**Scenario 2 — same line on both sides (rare):**
Both machines edit the exact same markdown line in the same 10-min window.
Syncthing picks a winner as usual, but git rebase surfaces a conflict with
merge markers. `vault-autopush.sh` aborts the rebase and logs the error; the
next scheduled run retries. User must resolve manually: `cd ~/vault && git
pull --rebase origin main`, edit files, `git rebase --continue`, `git push`.
This happens rarely (estimate: a few times per month).

**Scenario 3 — high-churn state files (ai-transcripts JSONL, LLM session DB):**
These are gitignored precisely to avoid churn. Both machines write their own
transcripts; Syncthing last-write-wins is fine because losing a transcript
line is cheap and the content is also in Claude's conversation cache.

## Gotchas and maintenance

- **`.stfolder/` inside `~/vault/`**: Syncthing marker folder. Machine-specific
  content. Currently tracked in git (minor noise in commits). Consider adding
  `.stfolder/` to `.gitignore` if it shows up in too many autopush commits.
- **`.syncthing.*.tmp`**: transient temp files Syncthing uses during atomic
  writes. If one gets caught by autopush's `git add -A`, commit and move on —
  they're tiny.
- **GitHub push warnings >50 MB**: some ai-transcripts tool-result txt files
  exceed GitHub's recommended max. Not fatal (hard limit is 100 MB). If a
  single file exceeds 100 MB, add its directory to `.gitignore` and `git rm
  --cached` it.
- **Submodule drift**: `i447/i446/i446-monorepo` is pinned to a commit SHA in
  the vault's `.gitmodules`. That commit can go stale. Autopush uses
  `submodule.recurse=false` so submodule changes don't block sync; if you
  want the vault to track a newer monorepo commit, `cd i447/i446/i446-monorepo
  && git pull && cd ../../.. && git add i447/i446/i446-monorepo && git
  commit -m "bump monorepo submodule"`.
- **Blog submodule**: `hcmp/o315/blog/` is gitignored on ix after its `.git/`
  was wiped during the Syncthing era. Re-init by `cd hcmp/o315 && rm -rf blog
  && git clone git@github.com:jonathanmckay/o315-blog-v3.git blog`, then
  remove the gitignore line.

## Related files

- `~/i446-monorepo/scripts/vault-autopush.sh` — the commit/rebase/push loop
- `~/.claude/projects/-Users-mckay/memory/project_vault_sync.md` — Claude's
  memory note that vault is dual-synced
- `~/.claude/projects/-Users-mckay/memory/project_vault_concurrent_writes.md`
  — Claude's write-and-verify discipline for vault edits
