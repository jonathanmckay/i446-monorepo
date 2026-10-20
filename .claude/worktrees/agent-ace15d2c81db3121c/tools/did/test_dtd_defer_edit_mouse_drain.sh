#!/bin/bash
# Regression (2026-08-27): "sometimes this happens in the input bar" — raw
# SGR mouse escape sequences (e.g. ^[[<0;16;15M / ^[[<0;16;15m) leaking into
# fzf's query line as literal text.
#
# done.sh (⌃⏎) and split.sh (ctrl-p) both reset mouse-tracking mode AND drain
# any bytes already queued in the tty buffer after their interactive prompt
# (2026-07-27 fix — see the drain comment on each). defer.sh (ctrl-d) and
# edit.sh (ctrl-g) only got the mode-reset half of that fix (2026-07-05); the
# drain was never ported to them, so a scroll/click during either prompt left
# its raw SGR bytes sitting in the tty buffer for fzf to read as text on
# resume. edit.sh additionally `exit 0`'d on a cancelled (blank) edit BEFORE
# reaching even the reset line, skipping cleanup entirely on that path.
set -e

SCRIPT="$HOME/i446-monorepo/tools/did/dtd.sh"
fail() { echo "FAIL: $1"; exit 1; }

extract_heredoc() {
  # $1 = start marker regex, $2 = end marker (bare string)
  python3 - "$SCRIPT" "$1" "$2" <<'EOF'
import re, sys
src = open(sys.argv[1]).read()
m = re.search(sys.argv[2] + r'\n(.*?)\n' + re.escape(sys.argv[3]), src, re.S)
assert m, f"heredoc {sys.argv[2]!r}..{sys.argv[3]!r} not found"
print(m.group(1))
EOF
}

DRAIN='while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty'

defer_body=$(extract_heredoc 'cat > "\$DTD_DEFER" << DEFEREOF' 'DEFEREOF')
edit_body=$(extract_heredoc 'cat > "\$DTD_EDIT" << EDITEOF' 'EDITEOF')

# 1. Both scripts drain the tty buffer after their interactive prompt, not
#    just reset the mouse mode.
echo "$defer_body" | grep -qF "$DRAIN" || fail "defer.sh missing tty-drain after prompt"
echo "PASS: defer.sh drains tty buffer after prompt"

echo "$edit_body" | grep -qF "$DRAIN" || fail "edit.sh missing tty-drain after prompt"
echo "PASS: edit.sh drains tty buffer after prompt"

# 2. The drain runs BEFORE any `exit 0` that follows the prompt read, so a
#    cancelled/invalid answer still cleans up (it must not be possible to
#    exit the script between the tty read and the drain).
defer_read_line=$(echo "$defer_body" | grep -n '^  read days < /dev/tty$' | head -1 | cut -d: -f1)
defer_drain_line=$(echo "$defer_body" | grep -nF "$DRAIN" | head -1 | cut -d: -f1)
defer_exit_line=$(echo "$defer_body" | grep -n 'invalid defer target.*exit 0' | head -1 | cut -d: -f1)
[[ -n "$defer_read_line" ]] || fail "defer.sh prompt read not found"
[[ -n "$defer_exit_line" ]] || fail "defer.sh invalid-input exit 0 not found"
(( defer_drain_line > defer_read_line )) || fail "defer.sh drain must come after the prompt read"
(( defer_drain_line < defer_exit_line )) || fail "defer.sh drain must come before the invalid-input exit 0"
echo "PASS: defer.sh drains before its invalid-input exit path"

edit_read_line=$(echo "$edit_body" | grep -n '^read edits < /dev/tty$' | head -1 | cut -d: -f1)
edit_drain_line=$(echo "$edit_body" | grep -nF "$DRAIN" | head -1 | cut -d: -f1)
edit_exit_line=$(echo "$edit_body" | grep -n 'exit 0' | head -1 | cut -d: -f1)
[[ -n "$edit_read_line" ]] || fail "edit.sh prompt read not found"
[[ -n "$edit_exit_line" ]] || fail "edit.sh cancel exit 0 not found"
(( edit_drain_line > edit_read_line )) || fail "edit.sh drain must come after the prompt read"
(( edit_drain_line < edit_exit_line )) || fail "edit.sh drain must come before the cancelled-edit exit 0"
echo "PASS: edit.sh drains before its cancel exit path"

echo "PASS: defer/edit prompts drain the tty buffer, matching done.sh/split.sh"
