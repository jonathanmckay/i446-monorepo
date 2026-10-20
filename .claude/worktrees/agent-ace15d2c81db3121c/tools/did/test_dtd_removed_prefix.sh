#!/bin/bash
# Regression test: tasks removed via the defer/split bindings must disappear
# from the dtd list even when the removed-file entry is a TRUNCATED PREFIX
# of the task name (fzf middle-truncates long names, and the bindings write
# the prefix before "…" to the removed file).
#
# Bug (2026-06-06): split a long-named task → split succeeded, but the list
# filter compared removed entries by exact equality, so the original task
# kept rendering in dtd.

set -e
SCRIPT="$HOME/i446-monorepo/tools/did/dtd.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Extract the list-generator heredoc (between LISTEOF markers)
sed -n "/^cat > \"\$DTD_LIST\" << 'LISTEOF'$/,/^LISTEOF$/p" "$SCRIPT" | sed '1d;$d' > "$TMP/list.sh"
[ -s "$TMP/list.sh" ] || { echo "FAIL: could not extract list generator"; exit 1; }
chmod +x "$TMP/list.sh"

# Fake cache with one long task + one short task
cat > "$TMP/cache.json" << 'JSON'
{"today": [
  {"id": "1", "content": "check wechat conversation on both phones and reply to pending threads (10) [10]", "labels": ["xk87"], "due": "2026-06-06"},
  {"id": "2", "content": "short task (5) [5]", "labels": ["i9"], "due": "2026-06-06"}
]}
JSON
echo '[]' > "$TMP/done.json"
touch "$TMP/skipped"

# Removed file holds the TRUNCATED prefix, as written by the split binding
echo "check wechat conversation on both" > "$TMP/removed"

OUT=$(zsh "$TMP/list.sh" "$TMP/cache.json" "$TMP/done.json" "$TMP/removed" "2026-06-06" 120 "$TMP/skipped")

if echo "$OUT" | grep -q "wechat"; then
  echo "FAIL: split/deferred task with truncated removed-entry still in list"
  exit 1
fi
echo "PASS: truncated removed-prefix filters the full task name"

if echo "$OUT" | grep -q "short task"; then
  echo "PASS: unrelated task still listed"
else
  echo "FAIL: unrelated task wrongly filtered"
  exit 1
fi

# Exact-match removal still works
echo "short task" > "$TMP/removed"
OUT=$(zsh "$TMP/list.sh" "$TMP/cache.json" "$TMP/done.json" "$TMP/removed" "2026-06-06" 120 "$TMP/skipped")
if echo "$OUT" | grep -q "short task"; then
  echo "FAIL: exact-match removal broken"
  exit 1
fi
echo "PASS: exact-match removal still works"

# Empty removed file filters nothing
: > "$TMP/removed"
OUT=$(zsh "$TMP/list.sh" "$TMP/cache.json" "$TMP/done.json" "$TMP/removed" "2026-06-06" 120 "$TMP/skipped")
if echo "$OUT" | grep -q "wechat" && echo "$OUT" | grep -q "short task"; then
  echo "PASS: empty removed file filters nothing"
else
  echo "FAIL: empty removed file wrongly filtered tasks"
  exit 1
fi

echo "All tests passed."
