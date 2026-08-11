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

## ROOT CAUSE IDENTIFIED (2026-05-24, all four machines collected)

| Machine | Vault present | `export-claude-transcripts.py` on cron? | Source `~/.claude/projects` |
|---------|---------------|-----------------------------------------|----------------------------|
| straylight | yes | NO (only copilot exporter cron'd) | active, generates new sessions |
| straylight-refit | yes | NO | active locally |
| **ix** | **yes** | **YES** (`15 * * * *`) | mostly stale — Mac mini rarely runs Claude |
| donnager | NO (Windows) | n/a | 1 session, no vault |

**The log is being written by ix's cron job.** ix has 364 cached Claude sessions in `~/.claude/projects`, but they were all exported previously and no new sessions are being created on ix (it's a Mac mini that mostly runs Excel + remote osascript, not interactive Claude). So every run correctly skips all 364 → logs "Exported 0 file(s), skipped 364 session(s)" forever.

**The "bug" is the log message**, not the export logic. The skip semantics are correct:
- `needs_refresh = any((not p.exists()) or p.stat().st_mtime < src_mtime for p in paths)`
- All sessions: output `.md` exists and is newer than source `.jsonl` → skipped → counted but nothing actually wrong.

**Missing coverage:** Straylight and Refit, where Claude actually runs interactively, have NO cron for the export script. So new interactive sessions on those boxes aren't being exported automatically. That's the real gap.

## Recommended fix

1. **Quiet the misleading log on ix:** change the script's final log line to only emit when `exported > 0`, or split: `print(f"Up to date ({skipped} cached sessions)")` when nothing exported.
2. **Add cron on Straylight (and optionally Refit):** Add `15 * * * * python3 ~/i446-monorepo/scripts/export-claude-transcripts.py >> ~/vault/i447/i446/ai-transcripts/claude-export.log 2>&1` to Straylight's crontab — that's where Dream + interactive Claude work happens.
3. **Optional: rename `claude-export.log` per-host** to disambiguate (currently the same file is written by multiple machines via Syncthing, racing each other).

## Status
- [x] Locate export script on Straylight
- [x] Collect log + script from donnager (Windows — no vault, n/a)
- [x] Collect log + script from ix (cron'd here)
- [x] Collect log + script from straylight-refit
- [x] Identify root cause (misleading log msg + missing cron on Straylight)
- [ ] Propose fix PR (this branch + a follow-up adding Straylight cron)
