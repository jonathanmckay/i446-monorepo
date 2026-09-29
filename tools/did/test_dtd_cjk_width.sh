#!/bin/zsh
# Regression test (2026-09-28): dtd's estimate column must be laid out by
# terminal DISPLAY width, not len(). CJK names (大孩子文学时间) are 2 cells per
# character, so len()-based padding pushed their (N)/[N] past the right edge.
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

grep -q "_dwidth(head) - _dwidth(est)" "$DTD" || fail "rjust_est still pads with len()"
grep -q "_dwidth(line) > cols - 7" "$DTD" || fail "truncation still measures with len()"

python3 - <<'PY'
import re
src = open("dtd.sh", encoding="utf-8").read()
start = src.index("import unicodedata as _ud")
end = src.index("# Build task list in priority order", start)
ns = {"re": re}
exec(compile(src[start:end], "<dtd-rjust>", "exec"), ns)
_dwidth, rjust_est, _dtrunc = ns["_dwidth"], ns["rjust_est"], ns["_dtrunc"]

assert _dwidth("大孩子文学时间") == 14 and _dwidth("1st hci") == 7
cols = 60
a = rjust_est("↻ 1st hci (15) [26]", cols)
b = rjust_est("↻ 大孩子文学时间 (30) [50]", cols)
# both rows must end their estimate at the same display column
assert _dwidth(a) == _dwidth(b) == cols - 8, (_dwidth(a), _dwidth(b))
assert a.endswith("(15) [26]") and b.endswith("(30) [50]")
# width-aware truncation never splits and never exceeds the budget
t = _dtrunc("大孩子文学时间abc", 9)
assert t == "大孩子文" and _dwidth(t) <= 9, t
print("cjk width checks passed")
PY
echo "PASS: dtd estimate column is display-width aware"
