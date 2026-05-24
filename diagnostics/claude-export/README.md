# claude-export.log: Exporting 0 Sessions Diagnostic

**Filed:** 2026-05-24 (Dream v5 card 6, JM grade B)

## Symptom
`~/vault/i447/i446/ai-transcripts/claude-export.log` repeatedly logs `Exported 0 file(s), skipped 364 session(s).` The export script appears to be running but rejecting every session.

## Logs to collect (per JM)
Collect the equivalent log from all three machines:
- **donnager**
- **ix**
- **straylight-refit**

For each: `~/vault/i447/i446/ai-transcripts/claude-export.log` plus any `claude-export*` script in `~/i446-monorepo` or `~/scripts`.

## Hypotheses
1. **Filter condition rejects everything** — likely a "since last export" timestamp that's set to "now" or future.
2. **Wrong source directory** — script looking at the wrong Claude session dir after a Claude CLI version upgrade (e.g. `~/.claude-cli/CurrentVersion/sessions` vs `~/.claude/projects`).
3. **Permissions/path mismatch** — script runs as cron but path resolves differently than interactive.

## Next steps
1. Find the writer (`grep -rln "Exported.*skipped.*session" ~ 2>/dev/null` — may live outside i446-monorepo)
2. Capture script source + cron entry on each machine
3. Test in isolation with a single known-recent session

## Status
- [ ] Locate export script on Straylight
- [ ] Collect log + script from donnager
- [ ] Collect log + script from ix
- [ ] Collect log + script from straylight-refit
- [ ] Identify root cause
- [ ] Propose fix PR


## Findings (Straylight, 2026-05-24)

**Writer identified:** `~/i446-monorepo/scripts/export-claude-transcripts.py` (12.5 KB, last touched 2026-04-23). Line 352 emits the `Exported N file(s), skipped M session(s).` message.

**Skip logic** (lines 237–247 in `export_session`):
```python
needs_refresh = any(
    (not p.exists()) or p.stat().st_mtime < src_mtime
    for p in paths
)
if not needs_refresh and not force:
    return 0  # counted as "skipped"
```
This is *correct* behavior — if a session's `.md` is newer than the source `.jsonl`, no re-export needed.

**Cron analysis (Straylight only so far):**
- `export-copilot-transcripts.py` IS on cron (`0 * * * *`)
- `export-claude-transcripts.py` is **NOT on cron**

**Hypothesis (revised):** the chronic "Exported 0, skipped 364" log entries imply *something* is invoking the script repeatedly (every minute? on a watcher?) without producing fresh source data — so every session correctly skips. Possible drivers:
1. A launchd job (not visible in `crontab -l`)
2. A wrapper script that calls it (e.g. `~/bin/sync-claude.sh`)
3. A fs-watcher that triggers on `~/.claude/` activity
4. Or the export ran once during a Claude CLI version migration that changed mtimes, then nothing has been written since because no new Claude sessions are being created on this machine (Copilot CLI is being used instead).

**Next** (needs ix + donnager + straylight-refit data):
- Run `collect-logs.sh` on each remote → commit results
- Compare cron + launchd config across machines
- Check `~/.claude/projects/*/sessions/` mtimes vs `~/vault/i447/i446/ai-transcripts/` mtimes to see if Claude sessions are actually being generated
