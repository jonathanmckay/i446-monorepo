#!/bin/zsh
# Feature test (2026-07-24): ctrl-v same-day block delay.
# Instead of pushing a task to the end of the list (old skip-style behavior),
# ctrl-v opens an inner fzf of the remaining 地支 blocks (character + pinyin +
# hours) and HIDES the task until the chosen block's hour arrives, at which
# point the watcher's block-boundary reload surfaces it again.
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# ── 1. Structural ────────────────────────────────────────────────────────────
grep -q 'ctrl-v:execute($DTD_BLOCKDELAY {+2})' "$DTD" \
  || fail "ctrl-v must invoke the block-delay script with the {+2} batch"
grep -q "DTD_BLOCKDELAY=" "$DTD" || fail "block-delay script must be defined"
grep -q "dtd-block-snooze.json" "$DTD" || fail "snooze file path missing"
for py in mao chen si wu wei shen you xu hai; do
  grep -q "'$py'" "$DTD" || fail "pinyin '$py' missing from block picker"
done
grep -q "DTD_POINTS" "$DTD" && fail "old ctrl-v points binding should be gone"

# Inner picker must paint reliably under cmux (bug 2026-07-24: an inline
# --height=13 fzf lost its first draw — black screen until a keypress).
# Full-screen fzf (no --height) owns the alternate screen like the outer
# dtd fzf and always paints; the script also sanes the tty and clears the
# stale execute() frame first (CPAP prompt precedent, 2026-07-21).
BLOCKBODY=$(sed -n '/<< BLOCKEOF/,/^BLOCKEOF/p' "$DTD")
printf '%s\n' "$BLOCKBODY" | grep 'fzf ' | grep -q -- '--height' \
  && fail "inner block picker must be full-screen — --height loses its first paint under cmux"
printf '%s\n' "$BLOCKBODY" | grep -q 'stty sane' \
  || fail "inner block picker must force sane tty modes before fzf"
printf '%s\n' "$BLOCKBODY" | grep -qF '\033[2J' \
  || fail "inner block picker must clear the stale frame before fzf"

# ── 2. Functional: generator hides snoozed ids until their hour ─────────────
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

gen() {
  HOME="$TMP" python3 "$TMP/gen.py" "$TMP/c.json" "$TMP/done.json" "$TMP/rm" \
    "$TODAY" 100 "$TMP/sk" "$TMP/tm" "$TMP/v" \
    | sed -E 's/\x1b\[[0-9;]*m//g' | cut -f1
}

# snoozed → future hour: hidden. arrived → its hour has come: visible.
cat > "$TMP/.local/state/jm/dtd-block-snooze.json" <<JSON
{"date":"$TODAY","snoozes":{"snoozed":$((NOW_H + 2)),"arrived":$NOW_H}}
JSON
out=$(gen)
echo "$out" | grep -q "xk22" && fail "snoozed task must be hidden before its block"
echo "$out" | grep -q "xk20" || fail "task whose block has arrived must reappear"
echo "$out" | grep -q "0t"   || fail "un-snoozed task must be unaffected"

# Stale (yesterday's) snooze file must be ignored entirely.
cat > "$TMP/.local/state/jm/dtd-block-snooze.json" <<JSON
{"date":"2000-01-01","snoozes":{"snoozed":$((NOW_H + 2))}}
JSON
gen | grep -q "xk22" || fail "stale-dated snooze file must not hide tasks"

# ── 3. Functional: the PYWRITE writer sets and clears snoozes ────────────────
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

echo "PASS: block-snooze hides until the chosen block and writer round-trips"
