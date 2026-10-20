#!/bin/zsh
# Regression (2026-07-23): expanding the terminal window left dtd rows at the
# launch-width "…" truncation forever — the width was baked into the reload
# command string ('${COLUMNS:-80}' at startup), so no reload could widen it.
# Fix: the list wrapper prefers $FZF_COLUMNS (exported by fzf to every bound/
# reload command, tracking the live window), and a resize binding triggers a
# reload so the reflow happens on the resize itself.
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# ── 1. Structural ───────────────────────────────────────────────────────────
grep -q 'argv\[5\]="\$FZF_COLUMNS"' "$DTD" || fail "list wrapper must override width with FZF_COLUMNS"
grep -q -- '--bind "resize:reload(\$DTD_RELOAD)+transform-header(\$DTD_HDRGEN)"' "$DTD" \
  || fail "resize event must trigger a reload"

# ── 2. Behavioural: same cache, wider FZF_COLUMNS → wider rows ─────────────
TMP=$(mktemp -d); trap "rm -rf $TMP" EXIT
python3 - "$DTD" "$TMP/gen.sh" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
m = re.search(r"cat > \"\$DTD_LIST\" << 'LISTEOF'\n(.*?)\nLISTEOF", src, re.DOTALL)
open(sys.argv[2], 'w').write(m.group(1))
PY

cat > "$TMP/c.json" <<'JSON'
{"0neon":[{"id":"a","content":"a very long habit name that truncates somewhere for sure honestly quite long (15) [15]","due":"2026-07-23","labels":["0neon"],"priority":1}],
 "today":[]}
JSON
echo '{"date":"2026-07-23","names":[]}' > "$TMP/done.json"
: > "$TMP/rm"; : > "$TMP/sk"; : > "$TMP/tm"; echo default > "$TMP/v"

widest() {  # widest display-field width; $1 = FZF_COLUMNS value or ""
  local out
  out=$(FZF_COLUMNS="$1" zsh "$TMP/gen.sh" "$TMP/c.json" "$TMP/done.json" "$TMP/rm" \
        2026-07-23 60 "$TMP/sk" "$TMP/tm" "$TMP/v" \
        | cut -f1 | sed -E 's/\x1b\[[0-9;]*m//g' | awk '{ if (length > m) m = length } END { print m+0 }')
  echo "$out"
}

narrow=$(FZF_COLUMNS="" widest "")
narrow=$(widest "")
wide=$(widest 140)
[[ "$wide" -gt "$narrow" ]] || fail "FZF_COLUMNS=140 must widen rows (narrow=$narrow wide=$wide)"
[[ "$narrow" -le 60 ]] || fail "fallback width must still bound rows (narrow=$narrow)"

echo "PASS: rows reflow with FZF_COLUMNS (narrow=$narrow wide=$wide); resize binding present"
