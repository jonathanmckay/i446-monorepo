#!/bin/zsh
# Regression test: dtd's ctrl-t cycles the list view (default priority order ->
# grouped by domain/project -> back). Added 2026-06-26.
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# ── 1. Structural wiring ────────────────────────────────────────────────────
grep -q 'DTD_VIEW="/tmp/dtd-\$DTD_ID.view"' "$DTD"      || fail "no DTD_VIEW state file"
grep -q 'DTD_VIEWTOGGLE=' "$DTD"                          || fail "no DTD_VIEWTOGGLE script"
grep -q 'ctrl-t:execute-silent(\$DTD_VIEWTOGGLE)' "$DTD" || fail "ctrl-t not bound to the view toggle"
grep -q 'ctrl-t: view' "$DTD"                            || fail "ctrl-t missing from the key hints"
# The view file must reach the generator (8th arg) in BOTH the list cmd and the
# auto-reload watcher, else a reload would render the wrong/empty view.
grep -q "DTD_LIST_CMD=.*'\$DTD_TIMER' '\$DTD_VIEW'" "$DTD"     || fail "DTD_LIST_CMD doesn't pass the view file"
grep -q "DTD_WATCH_RELOAD=.*'\$DTD_TIMER' '\$DTD_VIEW'" "$DTD" || fail "watcher reload doesn't pass the view file"
grep -q '"\$1" "\$2" "\$3" "\$4" "\$5" "\$6" "\$7" "\$8"' "$DTD" || fail "generator invocation doesn't forward arg 8"
grep -q '"\$DTD_VIEW" "\$DTD_VIEWTOGGLE"' "$DTD"          || fail "view files not cleaned up on exit"
# Cycle (not a one-way flip): the toggle advances through an ordered list.
grep -q 'views=(default project)' "$DTD"                 || fail "toggle is not a cycle over a view list"

# ── 2. Functional: generator groups by domain in project view ───────────────
TMP=$(mktemp -d); trap "rm -rf $TMP" EXIT
# Extract the embedded list-generator python.
python3 - "$DTD" "$TMP/gen.py" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
m = re.search(r"cat > \"\$DTD_LIST\" << 'LISTEOF'\n(.*?)\nLISTEOF", src, re.DOTALL)
pm = re.search(r'python3 -c "\n(.*?)\n" "\$1"', m.group(1), re.DOTALL)
open(sys.argv[2], 'w').write(pm.group(1).replace('\\"', '"'))
PY
cat > "$TMP/tq.json" <<'JSON'
{"today":[
 {"id":"a1","content":"alpha (10) [5]","due":"2026-06-26","labels":["i9"],"priority":4},
 {"id":"b1","content":"beta (10) [5]","due":"2026-06-26","labels":["m5x2"],"priority":4},
 {"id":"a2","content":"gamma (10) [5]","due":"2026-06-26","labels":["i9"],"priority":3}
]}
JSON
echo '{"date":"2026-06-26","names":[]}' > "$TMP/done.json"; : > "$TMP/rm"; : > "$TMP/sk"; : > "$TMP/tm"
gen() { python3 "$TMP/gen.py" "$TMP/tq.json" "$TMP/done.json" "$TMP/rm" 2026-06-26 100 "$TMP/sk" "$TMP/tm" "$1" \
        | sed -E 's/\x1b\[[0-9;]*m//g' | cut -f1 | sed -E 's/ +\(.*//'; }

echo default > "$TMP/v"
def_out=$(gen "$TMP/v")
# Default = priority tiers: gamma (p3) sorts above the p4 i9/m5x2 tasks, so the
# two i9 tasks are NOT adjacent.
echo "$def_out" | tr -d ' ' | tr '\n' ',' | grep -q '^alpha,beta,gamma,$' \
  || fail "default view order changed: $(echo $def_out | tr '\n' ' ')"

echo project > "$TMP/v"
proj_out=$(gen "$TMP/v")
# Project view: domain-grouped, each row tagged. Both i9 tasks adjacent, m5x2 last.
echo "$proj_out" | tr -d ' ' | tr '\n' ',' | grep -q '^i9alpha,i9gamma,m5x2beta,$' \
  || fail "project view not grouped+tagged by domain: $(echo $proj_out | tr '\n' ' ')"

echo "PASS: dtd ctrl-t cycles default <-> project, generator groups by domain"
