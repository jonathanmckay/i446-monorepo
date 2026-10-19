# vault-mobile

Backend for the native iOS vault app: read a declared subset of `~/vault`
offline, capture new notes offline, never edit existing files.

```
 phone (SwiftUI, SQLite)                  ix (always-on)
 ┌────────────────────┐   GET /manifest   ┌───────────────────────────────┐
 │ local copy of the  │ ◄──────────────── │ server.py :5571               │
 │ subset + FTS index │   GET /file?p=    │  manifest.py walks the subset │
 │                    │ ◄──────────────── │  declared in                  │
 │ outbox (captures)  │   POST /drop      │  vault/z_meta/phone-subset.md │
 │                    │ ────────────────► │  → z_ibx/phone/<capture>.md   │
 └────────────────────┘                   └───────────────────────────────┘
          │                                         │
   Tailscale serve  https://ix.tail9c51d5.ts.net/vault/        (tailnet)
   Funnel           https://ix.tail9c51d5.ts.net:8443/vault/   (public, token)
```

## Pieces

| File | Role |
|------|------|
| `manifest.py` | Subset config parser, gitignore-style globbing (`**` spans dirs, `*` does not), mtime+size hash cache (`~/.cache/vault-mobile/hash-cache.json`), hard caps. CLI: `python3 manifest.py ~/vault` prints counts and timing. |
| `server.py` | Flask app. Bearer tokens per device in `~/.config/vault-mobile/tokens.json` (`python3 server.py token <device>` mints; delete the entry to revoke). Routes: `/healthz`, `/manifest` (ETag/304), `/file?p=` (manifest-listed paths only), `/drop` (idempotent on capture id, 60/min), `/captures`. |
| `com.jm.vault-mobile.plist` | launchd agent on ix (`~/Library/LaunchAgents/`). Logs `/tmp/vault-mobile.{log,err}`. |
| `tests/` | `python3 -m pytest -q tests` (passes on 3.9 on ix and 3.13 on Straylight). |
| `vault/z_meta/phone-subset.md` | What ships. Edit the frontmatter; the manifest regenerates within 30 s. |

## Numbers (2026-10-19)

9,133 files, 23.9 MB in the default subset. Cold manifest 15 s (hashing),
warm 0.27 s. Whole vault for comparison: 16.8k md files, 7.8 GB.

## Capture contract

A capture is a new file `z_ibx/phone/YYYY-MM-DD-HHMMSS-<id4>-<device>.md`
with frontmatter `type: capture`, `source: phone/<device>`, `created_at`,
`id`. `/notes` drains the folder with the same routing rules as
`z_ibx/new-notes.md` and archives routed captures under
`z_ibx/archive/YYYY-MM/phone/`. Nothing on the phone ever writes to an
existing vault path, so there is nothing for Syncthing, git, or Obsidian
Sync to merge.

## Client guidance (from the 2026-10-19 design critique)

- Fetch `/manifest` with `If-None-Match` on every foreground; render the
  local copy immediately and diff in the background. BGAppRefreshTask is a
  bonus, not the freshness mechanism.
- Full-manifest set-diff on the client: deletions fall out naturally.
- Verify `X-Sha256` against the manifest hash; on mismatch (agent wrote
  mid-sync) re-fetch once.
- Show `generated_at` and outbox depth in the UI so staleness and failed
  drops are visible.
- Wikilinks outside the subset render as a "not on phone" stub.
- v1 is capture (App Intent + in-app) and plain note view with FTS5;
  markdown rendering comes after a week of real use.

## Ops

```bash
ssh ix 'launchctl kickstart -k gui/$(id -u)/com.jm.vault-mobile'   # restart
ssh ix 'tail -20 /tmp/vault-mobile.log'
ssh ix 'cd ~/i446-monorepo/tools/vault-mobile && /usr/bin/python3 server.py token imago-c'  # rotate
```
