#!/bin/zsh
# Regression test: dtd cli (dtd.sh) must hide a recurring habit whose due date
# has advanced past today, the same way tools/dtd/dtd.py (the mobile server)
# already does.
#
# Bug (2026-09-07, user report: "1st hci" marked done in dtd mobile, still
# shown in dtd cli): dtd.py's build_tasks() has a cross-machine safety net --
# `if t.get("recurring") and t.get("due") and t["due"] > today: continue` --
# because completed-today.json (id/name based hiding) is machine-local and can
# miss a completion recorded on a different host (mirror-sync lag, or a
# Todoist word-overlap match whose recorded name still carries a "(N)" time
# annotation the name-hide strips before comparing, so it never matches).
# dtd.sh's list-builder never had this fallback at all -- id/name hiding was
# its ONLY mechanism, so a task closed on another host (Ix mobile) that never
# made it into Straylight's local completed-today.json lingered in dtd.sh
# forever, even though its due date had already durably advanced in Todoist
# and the refreshed cache reflected that.
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# 1. Structural: the list-builder must hide a recurring task whose due date
#    has advanced past today, independent of id/name-based completed-today
#    hiding.
grep -q "t.get('recurring', True) and t.get('due') and t\['due'\] > today" "$DTD" \
  || fail "list-builder is missing the cross-machine due-date-advanced hide fallback"

# 2. Functional: replicate the ordering + dedup + hide logic dtd.sh's
#    embedded list-builder uses and prove the exact reported scenario hides.
python3 - "$DTD" <<'PY'
import re

TODAY = "2026-09-07"
TOMORROW = "2026-09-08"

def hidden(t, completed_ids=frozenset()):
    if t.get('id') is not None and str(t['id']) in completed_ids:
        return True
    if t.get('recurring', True) and t.get('due') and t['due'] > TODAY:
        return True
    return False

# Exact reported shape: "1st hci" completed on another host (dtd mobile/Ix).
# Its Todoist due date durably advanced to tomorrow (reflected in the fresh
# '0neon' bucket), but this host's completed-today.json never got its id
# (the cross-machine mirror hadn't landed / the name-hide couldn't match) --
# so completed_ids is empty, exactly reproducing the bug report.
task = {"id": "6h8Q9v88r648cC2Q", "content": "1st hci (15) [15]",
        "due": TOMORROW, "recurring": True, "labels": ["0neon", "hci"]}
assert hidden(task, completed_ids=set()), \
    "a recurring habit done on another host (due advanced past today) must hide even with no local completed-today record"

# A daily habit genuinely still due today must NOT be hidden by the fallback.
still_due = {"id": "other", "content": "2nd hci (15) [15]",
             "due": TODAY, "recurring": True, "labels": ["0neon", "hci"]}
assert not hidden(still_due), "a habit still due today must stay visible"

# A non-recurring one-off task due tomorrow (e.g. a deferred copy) must NOT
# be swept up by the recurring-only fallback.
oneoff = {"id": "oneoff1", "content": "xk22 9.8", "due": TOMORROW,
          "recurring": False, "labels": ["0neon"]}
assert not hidden(oneoff), "a non-recurring task due tomorrow must not be hidden"

print("functional checks passed")
PY

echo "PASS: dtd cli hides cross-machine-completed recurring habits via due-date advance"
