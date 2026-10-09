---
name: "1-i446"
description: "Weekly vault-health review. Runs the rule set in tools/vault/vault_health.py (singleton folders, duplicate note names, Syncthing, git autopush, skills backup, OneDrive Neon parity, Time Machine, 7-day alert sink), auto-fixes what is safe, writes z_meta/YYYY-MM-DD-1-i446.md, and then walks the review items with you one numbered decision at a time. Usage: /1-i446"
user-invocable: true
---

# /1-i446 — weekly vault health

The Sunday 04:40 cron on Ix runs the same script and files a `😈 1-i446 vault
health review` Todoist task whose description carries the review items (dtd's
agent gesture on that task opens Claude with the list). Running this skill by
hand does the same thing interactively, from whichever machine you are on
(off-Ix runs reach Ix over ssh for the pusher-side checks).

## Steps

1. Run it and show the summary table:
   ```bash
   python3 ~/i446-monorepo/tools/vault/vault_health.py
   ```
   Then print the `| Check | Status |` table from the report it names
   (`~/vault/z_meta/YYYY-MM-DD-1-i446.md`). Say what was auto-fixed.
2. Walk the review items **ten at a time, numbered, with a recommended option
   marked (a)** — the same format as the 2026-09-24 singleton review. Do not
   move, delete or rename anything until JM answers each number.
3. Apply the answers. For singleton folders: flatten (prefix + move up), merge
   into the canonical doc, delete, or record the folder in
   `singleton-audit.py`'s `EXCLUDE_PREFIXES` as a confirmed exception. For
   duplicate note names: merge into one canonical note (carry over any
   unique lines, then repoint the bare `[[name]]` links listed), rename one,
   or add the name to `vault_health.py`'s `DUP_NAMES_OK`. For
   sync/backup findings: fix what he approves; if a fix is not something a
   script should own, file a Todoist task instead.
4. Re-run the script. Zero review items closes its own Todoist task.

## Rules

- Auto-fix stays limited to what the script already does (a Syncthing rescan).
  A stuck git rebase, a missing Time Machine destination, a broken OneDrive
  mirror are decisions, not fixes.
- New rules go into `vault_health.py` as a `check_*` returning a `Finding`;
  set `review=True` only when JM has to decide something.
