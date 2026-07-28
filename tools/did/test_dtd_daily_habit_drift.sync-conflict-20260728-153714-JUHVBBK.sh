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
grep -q "_daily = ('0neon', '夜neon')" "$DTD" || fail "no daily-habit section set"
grep -q "bound = _tomorrow if key in _daily else today" "$DTD" || fail "per-section due bound not applied"

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
  {"id":"b","content":"done habit [5]","due":"2026-06-28","labels":["0neon"],"priority":1}
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

echo "PASS: drifted daily habit shows; completed-today + weekly-due-tomorrow hidden"
