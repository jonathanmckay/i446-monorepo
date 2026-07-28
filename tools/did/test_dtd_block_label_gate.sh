#!/bin/zsh
# Feature test (2026-07-27): 地支 block LABELS gate dtd rows.
# /todo "<task> 戌" stores the glyph as a Todoist label; the list generator
# hides the task until that block's hour arrives (durable, task-level analog
# of the ctrl-v snooze — survives restarts and day rollovers).
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# ── 1. Structural ────────────────────────────────────────────────────────────
grep -q "_BLOCK_LABEL_HOURS" "$DTD" || fail "block-label hour map missing"
grep -q "Block-labeled (地支 glyph label" "$DTD" || fail "row-loop label gate missing"

# ── 2. Functional: label gates by current hour ──────────────────────────────
TMP=$(mktemp -d); trap "rm -rf $TMP" EXIT
python3 - "$DTD" "$TMP/gen.py" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
m  = re.search(r"cat > \"\$DTD_LIST\" << 'LISTEOF'\n(.*?)\nLISTEOF", src, re.DOTALL)
pm = re.search(r'python3 -c "\n(.*?)\n" "\$1"', m.group(1), re.DOTALL)
open(sys.argv[2], 'w').write(pm.group(1).replace('\\"', '"'))
PY

TODAY=$(date +%Y-%m-%d)
NOW_H=$(date +%H | sed 's/^0//')
# Pick a future block glyph (start > now) and an arrived one (start <= now).
read FUT ARR <<< "$(python3 - "$NOW_H" <<'PY'
import sys
blocks = [('卯',4),('辰',6),('巳',8),('午',10),('未',12),('申',14),('酉',16),('戌',18),('亥',20)]
now = int(sys.argv[1])
fut = next((g for g, h in blocks if h > now), None)
arr = next((g for g, h in reversed(blocks) if h <= now), None)
print(fut or '', arr or '')
PY
)"

cat > "$TMP/c.json" <<JSON
{"0neon":[],"1neon":[],"today":[
  {"id":"fut","content":"evening thing (5) [5]","due":"$TODAY","labels":["i447","$FUT"],"priority":1},
  {"id":"arr","content":"arrived thing (5) [5]","due":"$TODAY","labels":["i447","$ARR"],"priority":1},
  {"id":"plain","content":"plain thing (5) [5]","due":"$TODAY","labels":["i447"],"priority":1}
]}
JSON
echo '{"date":"'$TODAY'","names":[]}' > "$TMP/done.json"
: > "$TMP/rm"; : > "$TMP/sk"; : > "$TMP/tm"; echo default > "$TMP/v"

out=$(HOME="$TMP" python3 "$TMP/gen.py" "$TMP/c.json" "$TMP/done.json" "$TMP/rm" \
  "$TODAY" 100 "$TMP/sk" "$TMP/tm" "$TMP/v" "$TMP/nope" \
  | sed -E 's/\x1b\[[0-9;]*m//g')

echo "$out" | grep -q "plain thing" || fail "unlabeled task must render"
if [[ -n "$FUT" ]]; then
  echo "$out" | grep -q "evening thing" && fail "task labeled $FUT must hide before its block"
fi
if [[ -n "$ARR" ]]; then
  echo "$out" | grep -q "arrived thing" || fail "task labeled $ARR must show once its block arrived"
fi

echo "PASS: 地支 block labels gate rows until their block hour"
