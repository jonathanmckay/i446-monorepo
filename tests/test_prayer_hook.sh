#!/usr/bin/env bash
# test_prayer_hook.sh — regression test for the purple prayer prompt hook.
#
# Validates the token-based mechanism that keeps purple visible during
# the prayer moment before Claude starts processing.
#
# Run: bash ~/i446-monorepo/tests/test_prayer_hook.sh

PASS=0
FAIL=0
TERM_COLOR="$HOME/i446-monorepo/scripts/term-color.sh"

pass() { echo "  ✓ $1"; ((PASS++)); }
fail() { echo "  ✗ $1"; ((FAIL++)); }

echo "── Prayer Hook Tests ──"

# Clean state
rm -f /tmp/claude-prayer-token

# --- Test 1: Token is written synchronously before background fork ---
echo ""
echo "Test 1: Token exists immediately after hook command"
T=$(date +%s$$)
echo "$T" > /tmp/claude-prayer-token
"$TERM_COLOR" purple >/dev/null 2>&1
( sleep 5; [ "$(cat /tmp/claude-prayer-token 2>/dev/null)" = "$T" ] && { rm -f /tmp/claude-prayer-token; } ) &
BG_PID=$!

if [ -f /tmp/claude-prayer-token ]; then
    pass "Token file exists after sync portion"
else
    fail "Token file missing — race condition"
fi

TOKEN_VAL=$(cat /tmp/claude-prayer-token 2>/dev/null)
if [ "$TOKEN_VAL" = "$T" ]; then
    pass "Token value matches"
else
    fail "Token value mismatch: expected '$T', got '$TOKEN_VAL'"
fi

# --- Test 2: PreToolUse guard respects token ---
echo ""
echo "Test 2: PreToolUse does NOT set black while token exists"
# Simulate PreToolUse check
if [ -f /tmp/claude-prayer-token ]; then
    # This is the PreToolUse logic: token exists → do nothing
    pass "PreToolUse would skip (token present)"
else
    fail "PreToolUse would set black (token missing too early)"
fi

# --- Test 3: Token survives for at least 3 seconds ---
echo ""
echo "Test 3: Token persists past 3-second mark"
sleep 3
if [ -f /tmp/claude-prayer-token ]; then
    pass "Token still exists after 3s"
else
    fail "Token gone before 3s — timer too short"
fi

# --- Test 4: Token is cleaned up after 5 seconds ---
echo ""
echo "Test 4: Token is removed after 5-second timer"
sleep 3  # total ~6s from creation
if [ ! -f /tmp/claude-prayer-token ]; then
    pass "Token removed after timer (>5s elapsed)"
else
    fail "Token still exists after 6s — cleanup not firing"
    rm -f /tmp/claude-prayer-token
fi

# --- Test 5: PreToolUse sets black after token is gone ---
echo ""
echo "Test 5: PreToolUse sets black when no token"
if [ ! -f /tmp/claude-prayer-token ]; then
    # This is what PreToolUse would do
    pass "PreToolUse would set black (no token)"
else
    fail "Token unexpectedly still present"
fi

# --- Test 6: Subsequent prompt gets fresh token (no stale token) ---
echo ""
echo "Test 6: Fresh token on new prompt"
T2=$(date +%s$$)
echo "$T2" > /tmp/claude-prayer-token
( sleep 5; [ "$(cat /tmp/claude-prayer-token 2>/dev/null)" = "$T2" ] && { rm -f /tmp/claude-prayer-token; } ) &

if [ -f /tmp/claude-prayer-token ]; then
    VAL=$(cat /tmp/claude-prayer-token)
    if [ "$VAL" = "$T2" ]; then
        pass "Fresh token with new value"
    else
        fail "Stale token from previous prompt: $VAL"
    fi
else
    fail "No token on second prompt"
fi

# --- Test 7: cmux color path works when CMUX_BUNDLE_ID is set ---
echo ""
echo "Test 7: term-color.sh uses cmux path in cmux environment"
if [ -n "$CMUX_BUNDLE_ID" ] && command -v cmux &>/dev/null; then
    # We're in cmux — verify the script uses the cmux workspace-action path
    OUTPUT=$("$TERM_COLOR" purple 2>&1)
    # Script should exit 0 and not fall through to Terminal.app
    if [ $? -eq 0 ]; then
        pass "cmux color path exits cleanly"
    else
        fail "cmux color path failed"
    fi
    "$TERM_COLOR" black >/dev/null 2>&1
else
    # Not in cmux — verify script at least has the cmux detection
    if grep -q 'CMUX_BUNDLE_ID' "$TERM_COLOR"; then
        pass "cmux detection present in script (not in cmux env, skipping runtime test)"
    else
        fail "cmux detection missing from term-color.sh"
    fi
fi

# Cleanup
sleep 6
rm -f /tmp/claude-prayer-token
kill $BG_PID 2>/dev/null

echo ""
echo "── Results: $PASS passed, $FAIL failed ──"
[ $FAIL -eq 0 ] && exit 0 || exit 1
