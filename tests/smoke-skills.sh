#!/usr/bin/env bash
# smoke-skills.sh — lightweight smoke tests for top skills
# Validates that dependencies exist, key files are readable, and APIs respond.
# Does NOT execute skills or modify any state.
#
# Usage: bash ~/i446-monorepo/tests/smoke-skills.sh

set -u
PASS=0
FAIL=0
WARN=0

pass() { echo "  PASS  $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
warn() { echo "  WARN  $1"; WARN=$((WARN+1)); }

echo "Smoke Tests — Top Skills"
echo "========================"
echo ""

# ── ibx / ibx0 / inbound ────────────────────────────────────────────────
echo "ibx / ibx0 / inbound:"
[ -f ~/i446-monorepo/tools/ibx/ibx0.py ] && pass "ibx0.py exists" || fail "ibx0.py missing"
[ -f ~/i446-monorepo/tools/ibx/-2n.py ] && pass "-2n.py exists" || fail "-2n.py missing"
[ -f ~/i446-monorepo/tools/ibx/inbound_wrapper.sh ] && pass "inbound_wrapper.sh exists" || fail "inbound_wrapper.sh missing"
python3 -c "from rich.console import Console" 2>/dev/null && pass "rich importable" || fail "rich not installed"
echo ""

# ── did ──────────────────────────────────────────────────────────────────
echo "did:"
[ -f ~/i446-monorepo/skills/claude-skills/did/SKILL.md ] && pass "SKILL.md exists" || fail "SKILL.md missing"
[ -f ~/i446-monorepo/skills/claude-skills/_lib/ix-osa.sh ] && pass "ix-osa.sh exists" || fail "ix-osa.sh missing"
[ -x ~/i446-monorepo/skills/claude-skills/_lib/ix-osa.sh ] && pass "ix-osa.sh executable" || fail "ix-osa.sh not executable"
echo ""

# ── next ─────────────────────────────────────────────────────────────────
echo "next:"
[ -f ~/i446-monorepo/skills/claude-skills/next/SKILL.md ] && pass "SKILL.md exists" || fail "SKILL.md missing"
curl -s -o /dev/null -w "%{http_code}" "https://api.todoist.com/rest/v2/tasks?limit=1" -H "Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5" 2>/dev/null | grep -q 200 && pass "Todoist API reachable" || warn "Todoist API unreachable"
echo ""

# ── 0r ───────────────────────────────────────────────────────────────────
echo "0r:"
[ -f ~/i446-monorepo/skills/claude-skills/0r/SKILL.md ] && pass "SKILL.md exists" || fail "SKILL.md missing"
[ -n "${TOGGL_API_KEY:-}" ] || [ -f ~/i446-monorepo/tools/personal-dashboard/.env ] && pass "Toggl env available" || warn "TOGGL_API_KEY not set"
echo ""

# ── 1n ───────────────────────────────────────────────────────────────────
echo "1n:"
[ -f ~/i446-monorepo/skills/claude-skills/1n/SKILL.md ] && pass "SKILL.md exists" || fail "SKILL.md missing"
python3 -c "import matplotlib" 2>/dev/null && pass "matplotlib importable" || fail "matplotlib not installed"
echo ""

# ── -2n ──────────────────────────────────────────────────────────────────
echo "-2n:"
[ -f ~/vault/g245/-1₦\ ,\ 0₦\ -\ Neon\ \{Build\ Order\}.md ] && pass "build order exists" || warn "build order file missing"
[ -d ~/vault/g245/v_logs ] && pass "v_logs dir exists" || warn "v_logs dir missing"
echo ""

# ── prep ─────────────────────────────────────────────────────────────────
echo "prep:"
[ -f ~/i446-monorepo/skills/claude-skills/prep/SKILL.md ] && pass "SKILL.md exists" || fail "SKILL.md missing"
[ -d ~/vault/h335/d359 ] || [ -d ~/vault/d359 ] && pass "d359 directory exists" || fail "d359 directory missing"
echo ""

# ── send ─────────────────────────────────────────────────────────────────
echo "send:"
[ -f ~/i446-monorepo/skills/claude-skills/send/SKILL.md ] && pass "SKILL.md exists" || fail "SKILL.md missing"
[ -f ~/i446-monorepo/tools/ibx/agency_mcp.py ] && pass "agency_mcp.py exists" || warn "agency_mcp.py missing"
echo ""

# ── book ─────────────────────────────────────────────────────────────────
echo "book:"
[ -f ~/i446-monorepo/skills/claude-skills/book/SKILL.md ] && pass "SKILL.md exists" || fail "SKILL.md missing"
[ -d ~/vault/hcmc/reviews ] && pass "reviews dir exists" || fail "reviews dir missing"
echo ""

# ── Dashboard services ───────────────────────────────────────────────────
echo "Dashboards:"
curl -s -o /dev/null -w "%{http_code}" http://localhost:5558/ 2>/dev/null | grep -q 200 && pass "personal dashboard (5558) running" || warn "personal dashboard not running"
curl -s -o /dev/null -w "%{http_code}" http://localhost:5555/ 2>/dev/null | grep -q 200 && pass "AI dashboard (5555) running" || warn "AI dashboard not running"
echo ""

# ── Ix connectivity ──────────────────────────────────────────────────────
echo "Infrastructure:"
ssh -o ConnectTimeout=2 -o BatchMode=yes ix echo OK 2>/dev/null | grep -q OK && pass "ix reachable" || warn "ix unreachable"
[ -f ~/.claude/ix-write-queue.jsonl ] && warn "ix write queue has $(wc -l < ~/.claude/ix-write-queue.jsonl | tr -d ' ') pending writes" || pass "ix write queue empty"
echo ""

# ── Summary ──────────────────────────────────────────────────────────────
echo "========================"
echo "Results: $PASS passed, $FAIL failed, $WARN warnings"
[ "$FAIL" -eq 0 ] && echo "All critical checks passed." || echo "Some checks failed — investigate above."
exit $FAIL
