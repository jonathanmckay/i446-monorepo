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
grep -q 'read _iv < /dev/tty' "$SCRIPT" || fail "cpap prompt must read from /dev/tty"
grep -F 'clean="\$clean \$_iv"' "$SCRIPT" >/dev/null || fail "cpap score must be appended to clean"
echo "PASS: cpap score read from tty and appended"

# 3. The prompt is gated behind a value-prompt task (_ip set) + an available tty
# (a plain completion isn't gated behind input).
grep -F '[[ -n "\$_ip" && -r /dev/tty ]]' "$SCRIPT" >/dev/null \
  || fail "prompt must be gated to value-prompt tasks + an available tty"
echo "PASS: prompt gated to value-prompt tasks with a tty"

# 3b. Regression (2026-07-21): in cmux, fzf's execute() leaves the alternate
# screen but the stale fzf frame stays visible, so the prompt was invisible and
# a blind ⌃⏎ (ESC CR) fed a literal ESC byte into the read, corrupting the
# completion name ("CPAP ␛" — did-fast can no longer match the task). The DONE
# script must (a) force sane tty modes, (b) clear the screen before prompting,
# and (c) keep only digits from the answer.
grep -F 'stty sane < /dev/tty' "$SCRIPT" >/dev/null \
  || fail "DONE prompt must force sane tty modes before read"
grep -F "printf '\033[2J\033[H→ %s: '" "$SCRIPT" >/dev/null \
  || fail "DONE prompt must clear the screen so it is visible over a stale frame"
grep -F '_iv=\${_iv//[^0-9]/}' "$SCRIPT" >/dev/null \
  || fail "DONE prompt answer must be sanitized to digits only"
echo "PASS: prompt clears screen, sanes tty, and keeps digits only"

# 3c. Behavioural: the sanitize step drops an ESC byte (blind ⌃⏎) so the
# completion name stays clean, while a real score still gets appended.
san() { zsh -c 'clean="CPAP"; _iv="$1"; _iv=${_iv//[^0-9]/}; [[ -n "$_iv" ]] && clean="$clean $_iv"; printf %s "$clean"' _ "$1"; }
[[ "$(san $'\x1b')" == "CPAP" ]] || fail "ESC input must sanitize to no-score completion"
[[ "$(san '2')" == "CPAP 2" ]] || fail "a typed score must be appended"
[[ "$(san $'\x1b2')" == "CPAP 2" ]] || fail "digits must survive sanitization when mixed with ESC"
echo "PASS: ESC input completes without score; real scores survive"

# 4. alt-enter routes through the transform router (so only cpap gets a tty and
#    every other completion stays flicker-free execute-silent).
alt_line=$(grep -n 'alt-enter:' "$SCRIPT" | head -1)
echo "$alt_line" | grep -q 'alt-enter:transform($DTD_DONE_ROUTER {2})' \
  || fail "alt-enter must route through the transform router: $alt_line"
echo "PASS: alt-enter routes through transform(\$DTD_DONE_ROUTER {2})"

# 5. The router emits execute for cpap and execute-silent otherwise.
# (execute-silent's shape changed 2026-08-01: it now runs the fast id-hide
# synchronously then backgrounds the full done.sh, hence 4 %s args not 2 —
# this assertion was stale from before that redesign and never re-checked.)
grep -F "printf 'execute(%s %s)' \"\$DTD_DONE\"" "$SCRIPT" >/dev/null \
  || fail "router must emit execute() for cpap"
grep -F "printf 'execute-silent(%s %s; %s %s >/dev/null 2>&1 &)' \"\$DTD_DONE_HIDE\"" "$SCRIPT" >/dev/null \
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
