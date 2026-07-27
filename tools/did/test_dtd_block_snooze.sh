#!/bin/zsh
# Feature test: ctrl-v same-day block delay — fzf-NATIVE picker (2026-07-27).
#
# History: every nested-UI picker failed under cmux (inner fzf never painted
# 2026-07-26 ×2; the printf/read menu was invisible and blind keystrokes
# skipped tasks instead 2026-07-27). The picker is now the outer fzf itself:
# ctrl-v arms $DTD_BLOCKPICK via execute-silent (NO terminal takeover), the
# list generator swaps task rows for block rows (BLOCK:<glyph> ids), and
# enter/⌃⏎ on a block row applies the snooze via $DTD_BLOCKAPPLY.
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# ── 1. Structural ────────────────────────────────────────────────────────────
grep -q 'ctrl-v:execute-silent($DTD_BLOCKARM {+2})' "$DTD" \
  || fail "ctrl-v must ARM the picker via execute-silent (no terminal takeover)"
grep -q 'ctrl-k:execute-silent($DTD_BLOCKARM {+2})' "$DTD" \
  || fail "ctrl-k must arm the same picker (user request 2026-07-27; skip is keyless)"
grep -q 'DTD_BLOCKARM=' "$DTD" || fail "arm script must be defined"
grep -q 'DTD_BLOCKAPPLY=' "$DTD" || fail "apply script must be defined"
grep -q 'dtd-block-snooze.json' "$DTD" || fail "snooze file path missing"
for py in mao chen si wu wei shen you xu hai; do
  grep -q "'$py'" "$DTD" || fail "pinyin '$py' missing from picker rows"
done
grep -q 'DTD_BLOCKDELAY' "$DTD" && fail "old nested-UI blockdelay script should be gone"
# enter + ⌃⏎ route picker rows to blockapply
[ "$(grep -c 'BLOCK:\* ]]; then' "$DTD")" -ge 2 ] \
  || fail "enter.sh AND done.sh must route BLOCK:* rows to blockapply"
# destructive bindings must ignore picker rows
[ "$(grep -c 'picker rows are not tasks' "$DTD")" -ge 3 ] \
  || fail "skip/delete/defer must guard against BLOCK:* ids"
# every list invocation passes the blockpick path (payload arg 9)
[ "$(grep -c "'\$DTD_VIEW' '\$DTD_BLOCKPICK'" "$DTD")" -ge 2 ] \
  || fail "list invocations must pass \$DTD_BLOCKPICK"
grep -q '"\$1" "\$2" "\$3" "\$4" "\$5" "\$6" "\$7" "\$8" "\$9"' "$DTD" \
  || fail "list wrapper must forward the 9th (blockpick) arg"

# ── 2. Functional: generator becomes the picker when armed ───────────────────
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
mkdir -p "$TMP/.local/state/jm"

cat > "$TMP/c.json" <<JSON
{"0neon":[
  {"id":"snoozed","content":"xk22 (20) [15]","due":"$TODAY","labels":["0neon"],"priority":1},
  {"id":"arrived","content":"xk20 (20) [15]","due":"$TODAY","labels":["0neon"],"priority":1},
  {"id":"plain","content":"0t (3) [10]","due":"$TODAY","labels":["0neon"],"priority":1}
 ],"1neon":[],"today":[]}
JSON
echo '{"date":"'$TODAY'","names":[]}' > "$TMP/done.json"
: > "$TMP/rm"; : > "$TMP/sk"; : > "$TMP/tm"; echo default > "$TMP/v"

gen() {  # $1 = optional blockpick file
  HOME="$TMP" python3 "$TMP/gen.py" "$TMP/c.json" "$TMP/done.json" "$TMP/rm" \
    "$TODAY" 100 "$TMP/sk" "$TMP/tm" "$TMP/v" "${1:-$TMP/nope}" \
    | sed -E 's/\x1b\[[0-9;]*m//g'
}

# 2a. Not armed → normal task list, no picker rows
out=$(gen)
echo "$out" | grep -q "0t" || fail "normal list must render when not armed"
echo "$out" | grep -q "BLOCK:" && fail "no picker rows when not armed"

# 2b. Armed → ONLY picker rows: future blocks + cancel, ids BLOCK:<glyph>
echo "plain" > "$TMP/armed"
out=$(gen "$TMP/armed")
echo "$out" | grep -q "0t" && fail "armed picker must replace the task list"
echo "$out" | grep -q "BLOCK:cancel" || fail "picker must offer cancel"
if [ "$NOW_H" -lt 18 ]; then
  echo "$out" | grep -q "BLOCK:戌" || fail "future block 戌 row missing"
fi
echo "$out" | grep -q "BLOCK:now" && fail "un-delay row must not show for un-snoozed ids"

# 2c. Armed with a currently-snoozed id → un-delay row appears
cat > "$TMP/.local/state/jm/dtd-block-snooze.json" <<JSON
{"date":"$TODAY","snoozes":{"snoozed":$((NOW_H + 2))}}
JSON
echo "snoozed" > "$TMP/armed"
gen "$TMP/armed" | grep -q "BLOCK:now" || fail "un-delay row must show for snoozed ids"

# 2d. Snoozed-id hiding in the NORMAL list (unchanged semantics)
out=$(gen)
echo "$out" | grep -q "xk22" && fail "snoozed task must be hidden before its block"
echo "$out" | grep -q "xk20" || fail "un-snoozed tasks must render"
cat > "$TMP/.local/state/jm/dtd-block-snooze.json" <<JSON
{"date":"2000-01-01","snoozes":{"snoozed":$((NOW_H + 2))}}
JSON
gen | grep -q "xk22" || fail "stale-dated snooze file must not hide tasks"

# ── 3. Functional: PYWRITE writer sets and clears snoozes ────────────────────
python3 - "$DTD" "$TMP/writer.py" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
m = re.search(r"<<'PYWRITE'\n(.*?)\nPYWRITE", src, re.DOTALL)
open(sys.argv[2], 'w').write(m.group(1))
PY
SN="$TMP/.local/state/jm/dtd-block-snooze.json"
rm -f "$SN"
python3 "$TMP/writer.py" "$SN" 申 id1 id2 | grep -q "⏰ → 申 14:00" \
  || fail "writer must confirm the chosen block"
python3 - "$SN" "$TODAY" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["date"] == sys.argv[2], "writer must stamp today's date"
assert d["snoozes"] == {"id1": 14, "id2": 14}, d["snoozes"]
PY
python3 "$TMP/writer.py" "$SN" now id1 | grep -q "shown again" \
  || fail "writer must support un-delay"
python3 - "$SN" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["snoozes"] == {"id2": 14}, d["snoozes"]
PY

echo "PASS: fzf-native block picker (arm → picker rows → apply) round-trips"
