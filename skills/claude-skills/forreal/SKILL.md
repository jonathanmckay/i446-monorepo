---
name: "forreal"
description: "Adversarial self-review of the work just claimed done in this conversation. Re-verify every claim against live state, pick holes, fix what is actually broken. Usage: /forreal [focus]"
user-invocable: true
---

# For Real?

The user does not trust that the preceding work was done right. Your job is to
prove them wrong with evidence, or prove them right and fix it. Assume at least
one thing IS wrong until the evidence says otherwise.

## Arguments

Optional focus (e.g. `/forreal the calendar events`). Without args, review the
most recent substantive piece of work in this conversation.

## Rules of engagement

- **Fresh evidence only.** Do not cite earlier tool output as proof; that is the
  thing under audit. Re-run the test, re-read the file, re-query the API.
- **Verify sideways.** Where the original check could share a blind spot with
  the original work (same query, same cache, same assumption), verify through a
  different path: a different API call, reading the persisted artifact, running
  the real binary, screenshotting the real UI.
- **"Should work" is a finding.** Any claim that was asserted but never
  observed working gets demoted to unverified and must be tested now.
- **No vacuous pass.** "Everything looks fine" is only acceptable as a table of
  claims, each with the concrete evidence that re-confirmed it.

## Steps

### 1. Reconstruct the claims

List every discrete claim made to the user in the work under review: files
edited, processes restarted, records created, bugs root-caused, tests passing.
Include implicit claims (e.g. "fixed" implies the running process actually
picked up the fix).

### 2. Attack each claim

For each, ask the questions that most often expose this household's failures:

- **Stale process / wrong target**: long-running TUIs (tg-tui, dtd, ibx) bake
  their scripts into `/tmp` at startup and read synced or symlinked copies
  (`~/i446-monorepo` → vault; `~/.claude/skills` ⇄ monorepo via Syncthing).
  Did the edit land where the running code actually reads, and was the process
  respawned after?
- **Partial completion**: did the fix cover the exact data the user hit
  (truncation, unicode, double spaces, overdue vs today), or just the happy
  path?
- **Wrong problem**: re-read the user's original message. Was the question
  actually answered, or an adjacent one?
- **Side effects**: anything killed, overwritten, or left behind (`/tmp` state,
  stray keystrokes sent to a TUI, half-written rows in Neon)?
- **Environment**: right account, calendar, machine, date, timezone? Writes via
  MCP land on the account the MCP is authed to, not necessarily the one
  intended.

### 3. Verdict table

Produce a table: claim → verdict (`verified` / `broken` / `unverifiable`) →
evidence (command run or artifact read, one line). Severity-rank the failures.

### 4. Fix

Fix every `broken` finding now, minimally. Re-verify each fix with the same
sideways method, not just the method that produced the original false pass.
For `unverifiable` items, say what access or action would settle it. Do not
fix nitpicks that change behavior the user did not ask about; list them
instead.

### 5. Report

Lead with the failures found and fixed. Then the verdict table. If genuinely
nothing was wrong, say so once, with the table as proof — no padding, no
self-congratulation.
