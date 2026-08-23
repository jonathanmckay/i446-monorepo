#!/bin/zsh
# Feature test (2026-07-28): markdown links in task content ("[(link)](url)"
# written by /todo) render as a clickable OSC 8 "(link)" in dtd — never the
# raw URL, which blows out truncation and reads as noise.
set -e
cd "$(dirname "$0")"
fail() { echo "FAIL: $1"; exit 1; }

REAL_LIB_DIR="$HOME/i446-monorepo/lib"  # captured BEFORE any HOME override below
TMP=$(mktemp -d); trap "rm -rf $TMP" EXIT
python3 - dtd.sh "$TMP/gen.py" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
m  = re.search(r"cat > \"\$DTD_LIST\" << 'LISTEOF'\n(.*?)\nLISTEOF", src, re.DOTALL)
pm = re.search(r'python3 -c "\n(.*?)\n" "\$1"', m.group(1), re.DOTALL)
open(sys.argv[2], 'w').write(pm.group(1).replace('\\"', '"').replace('\\\\', '\\'))
PY
TODAY=$(date +%F)
cat > "$TMP/c.json" <<JSON
{"0neon":[],"1neon":[],"today":[{"id":"L1","content":"review growth ideas [(link)](https://example.com/x?a=1&b=2) [15]","labels":["i9"],"priority":1,"due":"$TODAY"}]}
JSON
echo '{"date":"'$TODAY'","names":[]}' > "$TMP/done.json"
: > "$TMP/rm"; : > "$TMP/sk"; : > "$TMP/tm"; echo default > "$TMP/v"
out=$(HOME="$TMP" PYTHONPATH="$REAL_LIB_DIR" python3 "$TMP/gen.py" "$TMP/c.json" "$TMP/done.json" "$TMP/rm" \
  "$TODAY" 80 "$TMP/sk" "$TMP/tm" "$TMP/v" "$TMP/np")
printf '%s' "$out" | grep -q '(link)' || fail "visible text must be (link)"
printf '%s' "$out" | grep -qF $']8;;https://example.com/x?a=1&b=2' \
  || fail "row must carry an OSC 8 hyperlink to the URL"
printf '%s' "$out" | grep -q '](http' && fail "raw markdown link syntax must not render"
echo "PASS: markdown links render as clickable (link)"
