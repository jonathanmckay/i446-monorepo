#!/bin/bash
# Regression test: the ctrl-p split script must sanitize commas/semicolons
# from the task name before passing it to did-fast --points-only.
#
# Bug (2026-06-06): splitting "Rev on ground transit. Buy nightshade, 2x
# digital license plate, ..." passed the comma-bearing name to did-fast,
# which splits its input on [,;]. The name became multiple items: the real
# name (without [pts]/@label) fell to agent_needed and logged nothing, while
# the fragment after the last comma ("2 [10] @i447") was logged as a
# variable task named "2". Points landed only by luck.

set -e

SCRIPT="$HOME/i446-monorepo/tools/did/dtd.sh"

# Isolate the split-script heredoc (between the SPLITEOF markers)
SPLIT_BLOCK=$(sed -n '/--- Split script used by fzf ctrl-p binding ---/,/^SPLITEOF$/p' "$SCRIPT")

if [ -z "$SPLIT_BLOCK" ]; then
  echo "FAIL: could not locate split script block in dtd.sh"
  exit 1
fi

# 1. The split block must sanitize the name before the did-fast call
if echo "$SPLIT_BLOCK" | grep -q "safe_name = re.sub(r'\[,;\]+'"; then
  echo "PASS: split script sanitizes commas/semicolons from the task name"
else
  echo "FAIL: split script must strip [,;] from the name before did-fast"
  exit 1
fi

# 2. The did-fast --points-only call must use the sanitized name, not clean
if echo "$SPLIT_BLOCK" | grep -q "f'{safe_name} \[{pts_today}\] {label_arg}'"; then
  echo "PASS: did-fast call uses the sanitized name"
else
  echo "FAIL: did-fast --points-only call must use safe_name"
  exit 1
fi

# 3. Behavior: sanitized name must survive did-fast's item split ([,;]) as
#    a single item, keeping [pts] and @label attached to the name
RESULT=$(python3 -c "
import re
clean = 'Rev on ground transit. Buy nightshade, 2x digital license plate, bike rack; insurance'
safe_name = re.sub(r'[,;]+', ' ', clean)
arg = f'{safe_name} [10] @i447'
chunks = [c for c in re.split(r'[,;]', arg) if c.strip()]  # same split as did-fast parse_input
assert len(chunks) == 1, f'expected 1 item, got {len(chunks)}: {chunks}'
assert '[10]' in chunks[0] and '@i447' in chunks[0]
print('ok')")
if [ "$RESULT" = "ok" ]; then
  echo "PASS: comma-bearing name reaches did-fast as a single item with [pts] and @label"
else
  echo "FAIL: sanitized name still splits into multiple items"
  exit 1
fi

echo "All tests passed."
