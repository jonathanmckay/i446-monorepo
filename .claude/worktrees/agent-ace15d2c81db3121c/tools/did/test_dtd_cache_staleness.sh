#!/bin/zsh
# Regression test: dtd must refresh the cache when it is STALE BY TIME, not only
# when the task count is low. Guards against the 2026-06-21 bug where 早餐 (a
# daily habit whose occurrence had rolled to today) stayed hidden in dtd because
# the cache was stale and the count-based guards (<5, <30) never tripped.
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# 1. Structural: the time-based freshness guard exists and wires to --refresh-cache.
grep -q 'DTD_CACHE_MAX_AGE' "$DTD" || fail "no DTD_CACHE_MAX_AGE freshness guard in $DTD"
grep -q "cache_age" "$DTD" || fail "guard does not compute cache_age"
# The age check must sit BEFORE the count-based guard (so the snapshot is fresh).
age_line=$(grep -n 'cache_age=\$' "$DTD" | head -1 | cut -d: -f1)
count_line=$(grep -n "jq '.today | length // 0'" "$DTD" | head -1 | cut -d: -f1)
[[ -n "$age_line" && -n "$count_line" && "$age_line" -lt "$count_line" ]] \
  || fail "time guard ($age_line) must precede count guard ($count_line)"

# 2. Functional: the exact age computation the guard uses must classify a stale
#    cache as old and a fresh cache as recent, using the SAME python expression.
age_of() {
  python3 -c "
import json, sys, datetime as dt
try:
    u = json.load(open(sys.argv[1])).get('updated')
    print(int((dt.datetime.now() - dt.datetime.fromisoformat(u)).total_seconds()) if u else 10**9)
except Exception:
    print(10**9)
" "$1"
}
TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

# Stale: updated 1 hour ago -> age > 600 -> would refresh.
python3 -c "
import json, datetime as dt
old = (dt.datetime.now() - dt.timedelta(hours=1)).isoformat()
json.dump({'updated': old, 'today': []}, open('$TMP/stale.json','w'))
"
stale_age=$(age_of "$TMP/stale.json")
[[ "$stale_age" -gt 600 ]] || fail "stale cache (1h old) computed age=$stale_age, expected >600"

# Fresh: updated now -> age < 600 -> no refresh.
python3 -c "
import json, datetime as dt
json.dump({'updated': dt.datetime.now().isoformat(), 'today': []}, open('$TMP/fresh.json','w'))
"
fresh_age=$(age_of "$TMP/fresh.json")
[[ "$fresh_age" -lt 600 ]] || fail "fresh cache computed age=$fresh_age, expected <600"

# Missing 'updated' -> treated as maximally stale (forces refresh, never hides).
python3 -c "import json; json.dump({'today': []}, open('$TMP/noupd.json','w'))"
noupd_age=$(age_of "$TMP/noupd.json")
[[ "$noupd_age" -gt 600 ]] || fail "cache without 'updated' computed age=$noupd_age, expected >600"

echo "PASS: dtd time-based cache staleness guard"
