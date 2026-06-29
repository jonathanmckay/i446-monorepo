#!/bin/zsh
# Regression test: dtd must list -1neon block-ritual cards (سمش/-1g/-1ibx)
# FIRST, ahead of 0neon/1neon sections, goals, and everything else.
# Request 2026-06-29: "make it so the -1n tasks are in front."
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# 1. Structural: rituals are pulled from -1neon and prepended to all_tasks.
grep -q "rituals = \[t for t in today_tasks if '-1neon' in" "$DTD" || fail "rituals not extracted from -1neon"
grep -q "all_tasks = rituals + sections + goals + rest" "$DTD" || fail "rituals not prepended to all_tasks"

# 2. Functional: replicate the ordering and prove rituals lead.
python3 - <<'PY'
def prank(p): return -(p or 1)
today = "2026-06-29"
d = {
  "0neon": [{"id":"H1","content":"0t (3) [10]","labels":["0neon"],"due":today,"priority":1}],
  "today": [
    {"id":"R1","content":"😈 -1g","labels":["-1neon"],"due":today,"priority":1},
    {"id":"G1","content":"goal {10}","labels":["#0g","g245"],"due":today,"priority":1},
    {"id":"X1","content":"task [5]","labels":["i9"],"due":today,"priority":2},
  ],
}
sections = []
for key in ['0neon','1neon','关键路径','夜neon']:
    sections.extend([t for t in d.get(key,[]) if t.get('due') and t['due'] <= today])
today_tasks = [t for t in d.get('today',[]) if t.get('due') and t['due'] <= today]
rituals = [t for t in today_tasks if '-1neon' in t.get('labels', [])]
_nonritual = [t for t in today_tasks if '-1neon' not in t.get('labels', [])]
goals = [t for t in _nonritual if any(l in ('#0g','#-1g') for l in t.get('labels', []))]
rest = sorted([t for t in _nonritual if not any(l in ('#0g','#-1g') for l in t.get('labels', []))],
              key=lambda t: prank(t.get('priority')))
all_tasks = rituals + sections + goals + rest
ids = [t['id'] for t in all_tasks]
assert ids[0] == "R1", f"ritual must be first, got order {ids}"
assert ids.index("R1") < ids.index("H1") < ids.index("G1"), f"order wrong: {ids}"
print("functional checks passed")
PY

echo "PASS: dtd lists -1neon ritual cards first"
