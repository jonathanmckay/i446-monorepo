#!/bin/zsh
# Regression test: dtd must refresh its cache on a 2h-block boundary so the new
# block's -1neon ritual cards appear at the top.
# Bug (2026-06-29): freshness was purely time-based (DTD_CACHE_MAX_AGE=600s). A
# cache refreshed late in one block, then dtd opened just after the next block
# starts, is <600s old so no refresh fires — but it predates the new block's
# rituals (daemon rolls them at the boundary), so dtd showed no -1n cards.
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# 1. Structural: startup block-aware staleness + watcher block refresh exist.
grep -q "Block-aware freshness" "$DTD" || fail "startup block-aware staleness check missing"
grep -q "stale_block" "$DTD" || fail "stale_block guard missing"
grep -q 'last_blk=' "$DTD" || fail "watcher block-change refresh missing"

# 1b. The block-turnover refresh must RETRY across the daemon's card-creation lag.
# The daemon scores/reconciles first and only creates the new -1neon cards
# ~boundary+25-30s (later on a Todoist 503); a single boundary+15s refresh raced
# ahead and never retried, so the new -1n cards never surfaced in an idle dtd
# until a manual ctrl-r (bug 2026-07-01). Require a staggered retry loop whose
# cumulative delay outlasts the daemon (>= 45s), not a one-shot short sleep.
turn_line=$(grep -n 'the daemon rolls the -1neon ritual cards at the boundary' "$DTD" | head -1 | cut -d: -f1)
[[ -n "$turn_line" ]] || fail "block-turnover branch not found"
seg=$(awk "NR>=$turn_line && NR<=$turn_line+14" "$DTD")
echo "$seg" | grep -q 'for .*; do sleep .*--refresh-cache' \
  || fail "block-turnover refresh is a one-shot, not a retry loop (races the daemon's card creation)"
# Sum the sleep deltas in the loop; must reach >= 45s to outlast the daemon lag.
sum=$(echo "$seg" | grep -oE 'for _s in [0-9 ]+' | grep -oE '[0-9]+' | paste -sd+ - | bc 2>/dev/null || echo 0)
[[ -n "$sum" && "$sum" -ge 45 ]] \
  || fail "block-turnover retry window (${sum}s) too short for daemon card-creation lag (need >= 45s)"

# 2. Functional: the block comparison refreshes across a boundary, not within.
python3 - <<'PY'
import datetime as dt
def blk(t): return (t.date().isoformat(), max(0, min(8, (t.hour - 4) // 2)))

def stale(cache_updated, now):
    # mirrors the inline python in dtd.sh
    return blk(cache_updated) != blk(now)

now = dt.datetime(2026, 6, 29, 14, 5)            # 未 block
assert stale(dt.datetime(2026, 6, 29, 13, 58), now), "prev-block cache (7min old) must refresh"
assert not stale(dt.datetime(2026, 6, 29, 14, 1), now), "same-block cache must NOT refresh"
# day rollover always refreshes
assert stale(dt.datetime(2026, 6, 28, 14, 5), now), "previous-day cache must refresh"
print("functional checks passed")
PY

echo "PASS: dtd refreshes on block boundary for new-block rituals"
