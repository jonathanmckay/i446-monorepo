#!/bin/zsh
# Regression test: a daily 0neon/夜neon habit whose Todoist due date has drifted
# one day ahead (e.g. it got completed an extra time, advancing "every day" +1)
# must still appear on today's dtd list — the completed-today filter hides the
# ones actually done today. Strict due<=today silently dropped them (reported
# 2026-06-27: 0t due 2026-06-28 vanished). Weekly (1neon) / critical-path
# (关键路径) tasks keep the strict today bound.
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# ── 1. Structural: daily sections use a tomorrow bound, weekly/cp use today ──
# (2026-07-21: sections refactored to _sec(key, bound); the tomorrow bound is
# now gated to recurring cards so deferred one-off copies ("xk22 7.21", which
# carry the 0neon label with recurring: false) stay hidden until actually due.)
grep -q "_sec('0neon', _tomorrow) + _sec('夜neon', _tomorrow)" "$DTD" \
  || fail "daily sections must use the tomorrow bound"
grep -q "t.get('recurring', True) or t\['due'\] <= today" "$DTD" \
  || fail "tomorrow bound must be gated to recurring cards (default True)"
grep -q "oneneon = _sec('1neon', today)" "$DTD" || fail "weekly keeps the today bound"

# ── 2. Functional: extract the generator and check visibility ───────────────
TMP=$(mktemp -d); trap "rm -rf $TMP" EXIT
python3 - "$DTD" "$TMP/gen.py" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
m  = re.search(r"cat > \"\$DTD_LIST\" << 'LISTEOF'\n(.*?)\nLISTEOF", src, re.DOTALL)
pm = re.search(r'python3 -c "\n(.*?)\n" "\$1"', m.group(1), re.DOTALL)
open(sys.argv[2], 'w').write(pm.group(1).replace('\\"', '"'))
PY

cat > "$TMP/c.json" <<'JSON'
{"0neon":[
  {"id":"a","content":"0t (3) [10]","due":"2026-06-28","labels":["0neon"],"priority":1},
  {"id":"b","content":"done habit [5]","due":"2026-06-28","labels":["0neon"],"priority":1},
  {"id":"d","content":"xk22 6.27 (20) [25]","due":"2026-06-28","labels":["0neon"],"priority":1,"recurring":false},
  {"id":"e","content":"xk20 6.26 (30) [35]","due":"2026-06-27","labels":["0neon"],"priority":1,"recurring":false}
 ],
 "1neon":[{"id":"c","content":"weekly thing [10]","due":"2026-06-28","labels":["1neon"],"priority":1}],
 "today":[]}
JSON
echo '{"date":"2026-06-27","names":["done habit"]}' > "$TMP/done.json"
: > "$TMP/rm"; : > "$TMP/sk"; : > "$TMP/tm"; echo default > "$TMP/v"

out=$(python3 "$TMP/gen.py" "$TMP/c.json" "$TMP/done.json" "$TMP/rm" 2026-06-27 100 "$TMP/sk" "$TMP/tm" "$TMP/v" \
      | sed -E 's/\x1b\[[0-9;]*m//g' | cut -f1)

echo "$out" | grep -q "0t" || fail "daily habit due tomorrow (0t) was hidden"
echo "$out" | grep -q "done habit" && fail "habit completed today should be hidden"
echo "$out" | grep -q "weekly thing" && fail "weekly task due tomorrow should NOT show today"
# Deferred one-off copies (0neon label, recurring: false): hidden until due,
# visible once due (bug 2026-07-21: a just-deferred habit popped back into
# today's queue because the daily tomorrow bound applied to the copy too).
echo "$out" | grep -q "xk22 6.27" && fail "deferred copy due tomorrow must stay hidden today"
echo "$out" | grep -q "xk20 6.26" || fail "deferred copy due today must show"

echo "PASS: drifted daily habit shows; deferred copy hidden until due; completed-today + weekly hidden"
