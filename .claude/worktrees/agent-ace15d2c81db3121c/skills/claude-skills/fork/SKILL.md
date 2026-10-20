---
name: "fork"
description: "Fork a Claude Code session into a new cmux tab, referenced by title. No args forks the current session; /fork <words> forks the newest session whose title matches. Usage: /fork [title words] [--open]"
user-invocable: true
---

# Fork a session (/fork)

Opens a new cmux tab running `cs fork <query>` (`~/i446-monorepo/tools/cs/cs.py`),
which resolves the title to a session id and runs
`claude --resume <id> --fork-session -n "<title> ⑂"`. The fork keeps the full
context of the source session under a new id; the source is untouched.

## Steps

1. **Resolve the target.**
   - No args: the current session, `$CLAUDE_CODE_SESSION_ID` (cs matches on id prefix).
   - Args: the title words. `--open` means resume in place (`cs open`) instead of forking.
   ```bash
   cs id "<query>"          # prints the id; stderr lists runners-up if ambiguous
   cs ls "<query>" | head -3
   ```
   If `cs id` finds nothing, say so and stop. If ambiguous, show the top 3 lines
   from `cs ls` and proceed with the newest (cs's default); the user can re-run.
2. **Open a tab and launch.** Always a new tab, never this pane:
   ```bash
   cmux new-surface --type terminal --working-directory "$PWD" --focus true
   # parse "OK surface:N ..." then
   cmux respawn-pane --surface surface:N --command "cs fork '<query>'"
   ```
   Use `cs open` for `--open`. Quote the query with single quotes.
3. **Confirm in one line:** `forked "<title>" → new tab (surface:N)`.

## Notes

- `cs` ranks custom title (`/rename`, `-n`) > auto ai-title > first prompt > id prefix.
- Forking a long session pays the full context load on its first turn.
- Sessions on Ix: `cs fork --from ix '<query>'` copies the transcript here first.

## Response Style

Minimal. One line. Do NOT explain. Do NOT ask for confirmation.
