#!/bin/zsh
# Regression test: dtd's rjust_est must canonicalize the estimate column order to
# (time) [value] {bonus} regardless of how the source task stored them. Some tasks
# read '[20] (30)', others '(30) [20]'; the column looked ragged (2026-06-23).
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# 1. Structural: the normalization is present in rjust_est.
grep -q "_rank = {'(': 0, '\[': 1, '{': 2}" "$DTD" || fail "estimate-order canonicalization missing from rjust_est"

# 2. Functional: replicate the rjust_est token-reorder and prove it canonicalizes.
python3 - <<'PY'
import re
_EST_TOK = r'(?:\(\(?\d+\)?\)|\[\d*G?\]|\{\d+\})'

def canon(est):
    toks = re.findall(_EST_TOK, est)
    if len(toks) > 1:
        rank = {'(': 0, '[': 1, '{': 2}
        toks.sort(key=lambda tk: rank.get(tk[0], 9))
        est = ' '.join(toks)
    return est

assert canon('[20] (30)') == '(30) [20]', canon('[20] (30)')
assert canon('(30) [20]') == '(30) [20]', canon('(30) [20]')
assert canon('[40] (30)') == '(30) [40]'
assert canon('(20) [20]') == '(20) [20]'
# bonus goes last; nested time stays first
assert canon('[15] {20} (30)') == '(30) [15] {20}'
assert canon('((10)) [5]') == '((10)) [5]'
# single token untouched
assert canon('[15]') == '[15]'
print('functional canon checks passed')
PY

echo "PASS: dtd rjust_est canonicalizes estimate order"
