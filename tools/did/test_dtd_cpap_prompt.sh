#!/bin/bash
# Regression (2026-07-03): reinstate the CPAP completion prompt. When cpap is
# completed in dtd (alt-enter / ⌃⏎), it must ask for a 1-3 sleep-quality score
# and append it so did-fast writes the number to cpap's 0n column. This needs a
# tty, so the alt-enter binding must use execute (NOT execute-silent).
set -e

SCRIPT="$HOME/i446-monorepo/tools/did/dtd.sh"
fail() { echo "FAIL: $1"; exit 1; }

# 1. The DONE (complete) script prompts specifically for cpap quality 1-3.
grep -q 'CPAP quality (1-3)' "$SCRIPT" || fail "cpap 1-3 prompt missing from DONE script"
echo "PASS: cpap quality 1-3 prompt present"

# 2. It reads the answer from the tty and appends it to the task content.
# (The DONE script lives in a heredoc, so the source carries literal \$ — match
# fixed-string with -F rather than fighting grep escaping.)
grep -q 'read cpap_q < /dev/tty' "$SCRIPT" || fail "cpap prompt must read from /dev/tty"
grep -F 'clean="\$clean \$cpap_q"' "$SCRIPT" >/dev/null || fail "cpap score must be appended to clean"
echo "PASS: cpap score read from tty and appended"

# 3. Only cpap triggers the prompt (a plain completion isn't gated behind input).
grep -F '"\$clean_lower" == cpap && -r /dev/tty' "$SCRIPT" >/dev/null \
  || fail "prompt must be gated to cpap + an available tty"
echo "PASS: prompt gated to cpap with a tty"

# 4. alt-enter routes through the transform router (so only cpap gets a tty and
#    every other completion stays flicker-free execute-silent).
alt_line=$(grep -n 'alt-enter:' "$SCRIPT" | head -1)
echo "$alt_line" | grep -q 'alt-enter:transform($DTD_DONE_ROUTER {2})' \
  || fail "alt-enter must route through the transform router: $alt_line"
echo "PASS: alt-enter routes through transform(\$DTD_DONE_ROUTER {2})"

# 5. The router emits execute for cpap and execute-silent otherwise.
grep -F "printf 'execute(%s %s)' \"\$DTD_DONE\"" "$SCRIPT" >/dev/null \
  || fail "router must emit execute() for cpap"
grep -F "printf 'execute-silent(%s %s)' \"\$DTD_DONE\"" "$SCRIPT" >/dev/null \
  || fail "router must emit execute-silent() for non-cpap"
echo "PASS: router emits execute (cpap) / execute-silent (others)"

# 6. Behavioural: the router's routing decision, run against a mock resolver.
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/resolve.py" <<'PY'
import sys
print(sys.argv[2])  # echo the id back as the "content"
PY
cat > "$TMP/router.sh" <<ROUTEREOF
#!/bin/zsh
_id="\$1"
_t=\$(python3 "$TMP/resolve.py" x "\$_id" | tr '[:upper:]' '[:lower:]')
if [[ "\$_t" == cpap ]]; then printf 'execute(D %s)' "\$_id"; else printf 'execute-silent(D %s)' "\$_id"; fi
ROUTEREOF
[[ "$(zsh "$TMP/router.sh" cpap)" == "execute(D cpap)" ]] || fail "router should execute() for cpap"
[[ "$(zsh "$TMP/router.sh" CPAP)" == "execute(D CPAP)" ]] || fail "router should be case-insensitive"
[[ "$(zsh "$TMP/router.sh" 0t)" == "execute-silent(D 0t)" ]] || fail "router should execute-silent() for non-cpap"
echo "PASS: router routing verified (cpap→execute, case-insensitive, others→execute-silent)"

echo "All tests passed."
