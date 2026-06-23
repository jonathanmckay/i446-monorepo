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

# 1. Structural: the load must extract .names, not keep the whole dict.
grep -q "_done_raw.get('names'" "$DTD" || fail "completed-filter does not read .names from completed-today.json"
grep -q "completed = json.load(f)" "$DTD" && fail "completed still loaded as raw dict (the bug)"

# 2. Functional: replicate the load+filter logic and prove a DONE habit is hidden.
python3 - "$DTD" <<'PY'
import json, re, sys, tempfile, os
today = "2026-06-23"

# completed-today.json shape written by did-fast
done_file = tempfile.mktemp(suffix=".json")
json.dump({"date": today, "names": ["0t", "notes", "ibx s897"],
           "points": {"notes": 8}}, open(done_file, "w"))

# --- the FIXED load block (mirrors dtd.sh) ---
try:
    with open(done_file) as f:
        _done_raw = json.load(f)
    if isinstance(_done_raw, dict):
        completed = ([n.lower() for n in _done_raw.get('names', [])]
                     if _done_raw.get('date') == today else [])
    else:
        completed = [str(n).lower() for n in _done_raw]
except Exception:
    completed = []

def strip_ann(s):
    return re.sub(r'  +', ' ', re.sub(r' *\(\d*\)| *\[\d*\]| *\{\d*\}', '', s)).strip()

def hidden(content):
    clean = strip_ann(content).lower()
    prefix = clean.split(' - ')[0]
    return clean in completed or prefix in completed

assert completed == ["0t", "notes", "ibx s897"], f"names not extracted: {completed}"
assert hidden("0t (3) [10]"), "DONE habit 0t must be hidden from dtd"
assert hidden("notes (3) [8]"), "DONE habit notes must be hidden"
assert hidden("ibx s897 (5)"), "DONE multiword habit must be hidden"
assert not hidden("early hci (30) [20]"), "an undone task must NOT be hidden"

# stale-date file must NOT hide anything (guards against yesterday's file)
json.dump({"date": "2026-06-22", "names": ["0t"]}, open(done_file, "w"))
_r = json.load(open(done_file))
stale = [n.lower() for n in _r.get('names', [])] if _r.get('date') == today else []
assert stale == [], "stale-date completed file must not hide live tasks"

os.unlink(done_file)
print("functional checks passed")
PY

echo "PASS: dtd completed-filter hides DONE habits"
