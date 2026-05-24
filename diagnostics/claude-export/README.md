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

