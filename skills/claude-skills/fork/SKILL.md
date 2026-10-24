---
name: "fork"
description: "Fork a Claude Code session into a new cmux tab, referenced by title. No args forks the current session; /fork <words> forks the newest session whose title matches, on any Mac. Usage: /fork [title words] [--open]"
user-invocable: true
---

# Fork a session (/fork)

Opens a new cmux tab running `cs fork <id>` (`~/i446-monorepo/tools/cs/cs.py`),
which runs `claude --resume <id> --fork-session -n "<title> ⑂"`. The fork keeps the
full context of the source session under a new id; the source is untouched.

`cs` searches both Macs (ix, straylight) and copies the transcript over when the
match lives on the other one, so the session's host does not matter. What does
matter is where cmux runs: only on Straylight, and its socket refuses ssh clients.

## Steps

1. **Check the host first:** `command -v cmux`. If it prints nothing (this Claude
   runs on Ix), open with one line before anything else:
   `⚠ This session runs on Ix: I can find the session but can't open a Straylight tab; I'll put the command on your clipboard.`
2. **Resolve once, to an id.** Every later step uses the id, never the query
   (a new tab has no `$CLAUDE_CODE_SESSION_ID`, so a query could match differently there).
   - No args: `$CLAUDE_CODE_SESSION_ID`.
   - Args: the title words. `--open` means resume in place (`cs open`) instead of forking.
   ```bash
   cs id --everywhere "<query>"   # id on stdout; stderr: "is on <host>", runners-up, "<host> did not answer"
   cs ls "<query>" | head -3      # searches both Macs; '@sl'/'@ix' rows live on the other one
   ```
   If `cs id` finds nothing, say so (and pass on any "did not answer" line) and stop.
   If ambiguous, show the top 3 `cs ls` lines and proceed with the newest.
   A bare pane number ("session 4") only resolves on the Mac running cmux; without
   cmux, find the session by title and say which one you took.
3. **Launch.**
   - cmux present: always a new tab, never this pane:
     ```bash
     cmux new-surface --type terminal --working-directory "$PWD" --focus true
     # parse "OK surface:N ..." then
     cmux respawn-pane --surface surface:N --command "cs fork <id>"
     ```
   - No cmux (Ix): copy the command to Straylight's clipboard:
     ```bash
     ssh straylight "printf %s 'cs fork <id>' | pbcopy"
     ```
   `--open`: `cs open <id>`. It refuses a session that lives on the other Mac
   (resuming a copy would split one id into two diverging transcripts); report that
   and offer the fork instead.
4. **Confirm in one line:** `forked "<title>" → new tab (surface:N)`, or on Ix:
   `"<title>" (<host>): paste into a new Straylight tab — cs fork <id> is on your clipboard`.

## Notes

- `cs` ranks custom title (`/rename`, `-n`) > auto ai-title > first prompt > id prefix.
  Fork titles (`… ⑂`) rank just below ai titles, so the original outranks its forks.
- The asking session never matches its own `/fork <words>` prompt (only its id).
- Forking a long session pays the full context load on its first turn.

## Response Style

Minimal. One line (two on Ix, with the warning). Do NOT explain. Do NOT ask for confirmation.
