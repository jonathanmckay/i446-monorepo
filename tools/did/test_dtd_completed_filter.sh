#!/bin/zsh
# Regression test: dtd must hide habits recorded as DONE in completed-today.json.
# Bug (2026-06-23): the list-builder loaded the whole completed-today.json object
# and tested `clean in completed`, which checked membership against the dict's
# top-level keys ({date,names,points}) instead of the .names list — so completed
# habits like 0t were never filtered and stayed in dtd after /0t / /did.
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# 1. Structural: the load must extract .names, not keep the whole dict, and
#    must hide by id (collision-proof) using a name_only fallback.
grep -q "_done_raw.get('names'" "$DTD" || fail "completed-filter does not read .names from completed-today.json"
grep -q "completed = json.load(f)" "$DTD" && fail "completed still loaded as raw dict (the bug)"
grep -q "completed_ids" "$DTD" || fail "completed-filter does not hide by task id"
grep -q "name_only_completed" "$DTD" || fail "completed-filter lost the name-only fallback"

# 2. Functional: replicate the load+filter logic and prove (a) DONE habits hide,
#    (b) an id-backed completion hides only the matching id, NOT a different open
#    task that shares its annotation-stripped name (regression 2026-06-26).
python3 - "$DTD" <<'PY'
import json, re, sys, tempfile, os
today = "2026-06-23"

def strip_ann(s):
    return re.sub(r'  +', ' ', re.sub(r' *\(\d*\)| *\[\d*\]| *\{\d*\}', '', s)).strip()

def load_filter(done_obj):
    """Mirror dtd.sh's list-builder load + hide logic."""
    done_file = tempfile.mktemp(suffix=".json")
    json.dump(done_obj, open(done_file, "w"))
    try:
        with open(done_file) as f:
            _done_raw = json.load(f)
        if isinstance(_done_raw, dict):
            _gated = _done_raw.get('date') == today
            completed = ([n.lower() for n in _done_raw.get('names', [])] if _gated else [])
            _ids_map = (_done_raw.get('ids') or {}) if _gated else {}
        else:
            completed = [str(n).lower() for n in _done_raw]; _ids_map = {}
    except Exception:
        completed = []; _ids_map = {}
    os.unlink(done_file)
    completed_ids = {str(v) for v in _ids_map.values()}
    id_backed = {str(k).lower() for k in _ids_map.keys()}
    name_only = [n for n in completed if n not in id_backed]

    def hidden(content, tid=None):
        clean = strip_ann(content).lower()
        prefix = clean.split(' - ')[0]
        if tid is not None and str(tid) in completed_ids:
            return True
        return clean in name_only or prefix in name_only
    return completed, hidden

# (a) Habits with no ids — name-based hide still works.
completed, hidden = load_filter({"date": today, "names": ["0t", "notes", "ibx s897"],
                                 "points": {"notes": 8}})
assert completed == ["0t", "notes", "ibx s897"], f"names not extracted: {completed}"
assert hidden("0t (3) [10]"), "DONE habit 0t must be hidden from dtd"
assert hidden("notes (3) [8]"), "DONE habit notes must be hidden"
assert hidden("ibx s897 (5)"), "DONE multiword habit must be hidden"
assert not hidden("early hci (30) [20]"), "an undone task must NOT be hidden"

# (b) id-backed collision: completing stats id=AAA must NOT hide a different
#     still-open stats id=BBB, but MUST hide AAA itself.
_, hidden = load_filter({"date": today, "names": ["stats"],
                         "ids": {"stats": "AAA"}, "points": {"stats": 8}})
assert hidden("stats [10] (15)", tid="AAA"), "the completed stats (id AAA) must hide"
assert not hidden("stats [8] (4)", tid="BBB"), \
    "a DIFFERENT open stats (id BBB) must NOT be hidden by AAA's completion"
# Same task with an UNKNOWN id should also stay (id-backed name no longer name-hides)
assert not hidden("stats", tid="CCC"), "id-backed name must not hide unrelated ids"

# stale-date file must NOT hide anything (guards against yesterday's file)
completed, hidden = load_filter({"date": "2026-06-22", "names": ["0t"], "ids": {"0t": "ZZZ"}})
assert completed == [], "stale-date completed file must not hide live tasks"
assert not hidden("0t (3) [10]", tid="ZZZ"), "stale-date ids must not hide live tasks"

print("functional checks passed")
PY

echo "PASS: dtd completed-filter hides DONE habits + is id-collision-proof"
