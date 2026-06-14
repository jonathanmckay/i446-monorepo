#!/bin/bash
# Bug (2026-06-14): dtd completions silently not marked done. dtd.sh is sourced
# into a long-lived interactive shell, so $$ is the shell PID and is identical
# on every re-run. All ~23 temp paths used /tmp/dtd-$$, so a re-entrant launch
# (suspend an open picker, re-source) collided on the same FIFO/temp files; one
# run's `exec 3>&-` + cleanup killed another run's background worker, and
# completions written to the now-orphaned FIFO were dropped. Fix: a per-launch
# DTD_ID isolates every run's temp paths, plus a dead-shell sweep for orphans.

set -e
SCRIPT="$HOME/i446-monorepo/tools/did/dtd.sh"

# 1. A per-launch unique id is defined, prefixed by $$ (for the sweep) plus a
#    uniquifier so re-sources in the SAME shell don't collide.
if grep -Eq '^DTD_ID="\$\$-.*\$RANDOM"' "$SCRIPT"; then
  echo "PASS: DTD_ID defined with \$\$ prefix + \$RANDOM uniquifier"
else
  echo "FAIL: DTD_ID must be defined as \$\$-...-\$RANDOM"
  exit 1
fi

# 2. No session temp path may use the collision-prone bare /tmp/dtd-$$ token.
if grep -q '/tmp/dtd-\$\$' "$SCRIPT"; then
  echo "FAIL: bare /tmp/dtd-\$\$ session-temp path still present (will collide):"
  grep -n '/tmp/dtd-\$\$' "$SCRIPT"
  exit 1
fi
echo "PASS: no bare /tmp/dtd-\$\$ session-temp paths remain"

# 3. The FIFO and friends are keyed on $DTD_ID.
for v in DTD_FIFO DTD_PUSHED DTD_PROCESSED DTD_SESSION DTD_CACHE_FILE DTD_ENTER DTD_DONE; do
  if ! grep -q "^${v}=\"/tmp/dtd-\$DTD_ID" "$SCRIPT"; then
    echo "FAIL: $v not keyed on \$DTD_ID"
    exit 1
  fi
done
echo "PASS: FIFO/handler temp paths keyed on \$DTD_ID"

# 4. The agent prompt file inside the quoted heredoc keeps its runtime $$ (it is
#    the agent.sh CHILD's pid, deliberately decoupled; converting it would break
#    it). It must NOT have been rewritten to $DTD_ID.
if grep -q 'PROMPT_FILE="/tmp/dtd-agent-\$\$\.md"' "$SCRIPT"; then
  echo "PASS: agent.sh runtime PROMPT_FILE keeps child-pid \$\$"
else
  echo "FAIL: agent PROMPT_FILE should stay /tmp/dtd-agent-\$\$.md (runtime child pid)"
  exit 1
fi

# 5. Functional: ids differ across two draws in one shell ($$ held constant).
RES=$(zsh -c '
  id1="$$-$(date +%s)-$RANDOM"; id2="$$-$(date +%s)-$RANDOM"
  [[ "$id1" != "$id2" ]] && echo ok || echo collide')
[[ "$RES" == ok ]] && echo "PASS: two launches in one shell get distinct ids" || {
  echo "FAIL: ids collided in same shell"; exit 1; }

# 6. Functional: the dead-shell sweep removes an orphan from a dead pid (old
#    mtime) but keeps this shell's files and recent dead-pid files.
RES=$(zsh -c '
  d=$(mktemp -d); dead=999999
  touch -t 202001010000 "$d/dtd-$dead-1-1.fifo"   # dead+old  -> sweep
  touch                 "$d/dtd-$dead-2-2.fifo"   # dead+new  -> keep (mtime guard)
  touch -t 202001010000 "$d/dtd-$$-3-3.fifo"      # live+old  -> keep (alive)
  for _f in $d/dtd-<->-*(Nmh+1); do
    _pid=${${_f:t}#dtd-}; _pid=${_pid%%-*}
    [[ "$_pid" == <-> ]] && ! kill -0 "$_pid" 2>/dev/null && rm -f "$_f"
  done
  out=""
  [[ ! -e "$d/dtd-$dead-1-1.fifo" ]] && out="${out}a"
  [[   -e "$d/dtd-$dead-2-2.fifo" ]] && out="${out}b"
  [[   -e "$d/dtd-$$-3-3.fifo"   ]] && out="${out}c"
  rm -rf "$d"; echo "$out"')
[[ "$RES" == abc ]] && echo "PASS: sweep removes dead+old, keeps recent + live" || {
  echo "FAIL: sweep behavior wrong (got '$RES', want 'abc')"; exit 1; }

echo "All tests passed."
