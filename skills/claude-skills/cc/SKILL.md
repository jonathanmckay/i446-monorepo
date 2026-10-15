---
name: "cc"
description: "Launch cc (the tracked claude wrapper) in a new cmux tab. Plain words resume the session with that title (fork if it is live elsewhere); no args starts a fresh session. Usage: /cc [title words or claude args]"
user-invocable: true
---

# Launch cc in a new tab (/cc)

`cc` (`~/vault/i447/i446/claude-tracked`) already does the title resolution:
plain words go through `cs id --title` and become `--resume <id>` (or a fork if
that session is live in another pane); flags pass straight to claude. This skill
only opens a new cmux tab so the launch never nests inside the current pane.

## Steps

1. Open a tab in the current directory, then respawn it with the command. The respawned
   surface has only cmux's shims on PATH and no aliases, so run through an interactive zsh
   and use the wrapper's full path; the trailing `exec zsh -il` keeps the tab open after
   claude exits. Pass the args through verbatim.
   ```bash
   cmux new-surface --type terminal --working-directory "$PWD" --focus true
   # parse "OK surface:N ..." then
   cmux respawn-pane --surface surface:N --command "zsh -ic '~/vault/i447/i446/claude-tracked <args>'; exec zsh -il"
   ```
   With no args: `--command "zsh -ic '~/vault/i447/i446/claude-tracked'; exec zsh -il"`.
   Never write `cc` in the command: in that shell `cc` is the C compiler.
2. Confirm in one line: `cc <args> → new tab (surface:N)`.

## Response Style

Minimal. One line. Do NOT explain. Do NOT ask for confirmation.
