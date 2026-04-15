#!/bin/bash
# Hook: runs on UserPromptSubmit. If prompt is /did, outputs next-task suggestions.
# Synchronous — output appears in context before Claude starts reasoning.
INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt' 2>/dev/null) || exit 0

# Only trigger on /did commands
[[ "$PROMPT" == /did* ]] || exit 0

# Extract habit name: strip "/did " prefix, take first item (before comma/semicolon),
# strip time suffix (trailing number), strip date suffix
HABIT=$(echo "$PROMPT" | sed 's|^/did ||' | cut -d',' -f1 | cut -d';' -f1 | sed 's/ [0-9]*$//' | sed 's/ yesterday$//' | sed 's| [0-9]*/[0-9]*$||' | xargs)

# Run next-task script
RESULT=$(python3 ~/i446-monorepo/tools/did/next-task.py "$HABIT" 2>/dev/null)

if [ -n "$RESULT" ]; then
  echo "$RESULT"
fi
