#!/bin/zsh
# Regression test: dtd list order is fixed (user spec 2026-06-29):
#   -1n (-1neon rituals) → -1g (#-1g goals) → 0n (0neon+夜neon) → 1n (1neon)
#   → 0g (#0g goals) → critical-path (关键路径) → rest.
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# 1. Structural: the explicit ordered concatenation must be present.
grep -q "all_tasks = rituals + neg1g + zeroneon + oneneon + zerog + critical + rest" "$DTD" \
  || fail "dtd all_tasks order changed from -1n/-1g/0n/1n/0g"
grep -q "rituals = \[t for t in today_tasks if _has(t, '-1neon')\]" "$DTD" || fail "rituals group missing"
grep -q "neg1g = " "$DTD" || fail "-1g group missing"
grep -q "zerog = " "$DTD" || fail "0g group missing"

# 2. Functional: replicate the grouping and prove the order.
python3 - <<'PY'
def prank(p): return -(p or 1)
today = "2026-06-29"; tomorrow = "2026-06-30"
d = {
  "0neon": [{"id":"N1","content":"0t","labels":["0neon"],"due":today,"priority":1}],
  "夜neon": [{"id":"Y1","content":"evening","labels":["夜neon"],"due":today,"priority":1}],
  "1neon": [{"id":"W1","content":"1s","labels":["1neon"],"due":today,"priority":1}],
  "关键路径": [{"id":"K1","content":"crit","labels":["关键路径"],"due":today,"priority":1}],
  "today": [
    {"id":"R1","content":"😈 -1ibx","labels":["-1neon"],"due":today,"priority":4},
    {"id":"GA","content":"block goal {8}","labels":["#-1g"],"due":today,"priority":1},
    {"id":"ZG","content":"0g goal {30}","labels":["#0g"],"due":today,"priority":1},
    {"id":"RX","content":"plain [5]","labels":["i9"],"due":today,"priority":2},
  ],
}
def _sec(key, bound): return [t for t in d.get(key,[]) if t.get('due') and t['due'] <= bound]
today_tasks = [t for t in d.get('today',[]) if t.get('due') and t['due'] <= today]
def _has(t,l): return l in t.get('labels',[])
rituals = [t for t in today_tasks if _has(t,'-1neon')]
neg1g = [t for t in today_tasks if _has(t,'#-1g') and not _has(t,'-1neon')]
zeroneon = _sec('0neon',tomorrow) + _sec('夜neon',tomorrow)
oneneon = _sec('1neon',today)
zerog = [t for t in today_tasks if _has(t,'#0g') and not _has(t,'-1neon') and not _has(t,'#-1g')]
_placed = lambda t: _has(t,'-1neon') or _has(t,'#-1g') or _has(t,'#0g')
critical = _sec('关键路径',today)
rest = sorted([t for t in today_tasks if not _placed(t)], key=lambda t: prank(t.get('priority')))
order = [t['id'] for t in rituals + neg1g + zeroneon + oneneon + zerog + critical + rest]
assert order == ["R1","GA","N1","Y1","W1","ZG","K1","RX"], f"wrong order: {order}"
print("functional checks passed")
PY

echo "PASS: dtd list order is -1n / -1g / 0n / 1n / 0g / critical / rest"
