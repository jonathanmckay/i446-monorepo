#!/bin/zsh
# Regression test: dtd's ctrl-p split must handle {N} (0g bonus) tasks, not
# just [N] (domain points). Before this fix, total extraction only matched
# square brackets, so splitting a {50} task read total as "?", forced
# remaining_pts to 0 regardless of input, and both halves lost their {N}
# marker entirely -- the "remaining" task got renamed with no points
# annotation at all and the "done today" portion was credited via [N] (wrong
# bracket -> wrong column) instead of {N} -> 0分 col Q. User report
# 2026-07-28: "I tried to split a 0g, but it seems to have instead
# disappeared from the list."
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

split=$(awk "/cat > \"\\\$DTD_SPLIT\" << 'SPLITEOF'/{f=1;next} /^SPLITEOF/{f=0} f" "$DTD")
[[ -n "$split" ]] || fail "could not extract DTD_SPLIT body"

# 1. Point extraction falls back to {N} when [N] isn't present, and tracks
#    which bracket style was found.
echo "$split" | grep -q "grep -oE '\\\\{\[0-9\]+\\\\}'" || fail "no {N} fallback extraction"
echo "$split" | grep -q "bracket='{'; close='}'" || fail "curly match does not set bracket style"

# 2. The bracket style is threaded through to the python block instead of
#    being hardcoded to square brackets everywhere.
echo "$split" | grep -q "open_b, close_b = sys.argv\[10\], sys.argv\[11\]" || fail "python does not read bracket-style args"
echo "$split" | grep -q '"\$bracket" "\$close"' || fail "bracket/close not passed to python invocation"

# 3. Today's posthoc credit and the remainder's new content both use the
#    preserved bracket style, not a hardcoded [N].
echo "$split" | grep -q "posthoc_content = f'{today_label} ({duration or pts_today}) {open_b}{pts_today}{close_b}'" \
  || fail "posthoc content hardcodes square brackets"
echo "$split" | grep -q "{open_b}{remaining_pts}{close_b}. if remaining_pts > 0" \
  || fail "remainder content hardcodes square brackets"

# 4. The old hardcoded '[' / ']' forms must be gone from the parts we fixed
#    (a regression here would silently reintroduce the bug).
echo "$split" | grep -qF "[{pts_today}]" && fail "posthoc still hardcodes [N] (old bug)"
echo "$split" | grep -qF "[{remaining_pts}]" && fail "remainder still hardcodes [N] (old bug)"

# 5. Domain-label lookup (only meaningful for [N] tasks) is gated on the
#    square-bracket case -- a {N} 0g task must not also get a domain label,
#    which would double-credit (did-fast.py's documented 2026-07-27 bug).
echo "$split" | grep -q "if open_b == '\['" || fail "label lookup not gated on square-bracket style"

# 6. did-fast credit call uses the preserved bracket style, not a hardcoded [N].
echo "$split" | grep -qF "f'{safe_name} {open_b}{pts_today}{close_b} {label_arg}'" \
  || fail "did-fast credit call hardcodes square brackets"

echo "PASS: dtd split preserves {N} (0g) vs [N] (domain) point style through both halves"
