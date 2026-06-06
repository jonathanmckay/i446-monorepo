#!/bin/bash
# Regression: ctrl-d (defer) blocked fzf for the full Todoist round-trip
# (3-10s) because the defer script ran synchronously inside execute-silent.
# The script must return immediately (optimistic hide + header), run the
# network work detached, and roll back the hide on failure.
set -e
cd "$(dirname "$0")"

python3 - <<'EOF'
import re, subprocess, tempfile, os, time

src = open("dtd.sh").read()
m = re.search(r'cat > "\$DTD_DEFER" << DEFEREOF\n(.*?)\nDEFEREOF', src, re.S)
assert m, "DTD_DEFER heredoc not found"
body = m.group(1)

tmp = tempfile.mkdtemp()
paths = {k: os.path.join(tmp, k) for k in ("hdr", "removed", "pushed", "processed", "journal")}
for p in paths.values():
    open(p, "w").close()

# Simulate heredoc expansion (creation-time vars + \$ -> $)
body = body.replace("$DTD_HDR", paths["hdr"]).replace("$DTD_REMOVED", paths["removed"])
body = body.replace("$DTD_PUSHED", paths["pushed"]).replace("$DTD_PROCESSED", paths["processed"])
body = body.replace("$UNDO_FAST", "/usr/bin/true").replace("$DTD_JOURNAL", paths["journal"])
body = body.replace("\\$", "$")

stub = os.path.join(tmp, "defer_stub.py")
open(stub, "w").write(
    'import time, json\ntime.sleep(2)\n'
    'print(json.dumps({"target_date": "2026-06-07", "claimed_points": 2, "remaining_points": 10}))\n')
body = body.replace('DEFER_FAST="$HOME/i446-monorepo/tools/did/defer-fast.py"',
                    f'DEFER_FAST="{stub}"')
script = os.path.join(tmp, "defer.sh")
open(script, "w").write(body)
os.chmod(script, 0o755)

# 1. Wrapper must NOT block on the 2s network stub
t0 = time.time()
subprocess.run([script, "test task (5) [10]"], timeout=10)
elapsed = time.time() - t0
assert elapsed < 1.0, f"defer wrapper blocked for {elapsed:.1f}s — must be async"
assert "test task" in open(paths["removed"]).read(), "optimistic hide missing"
assert "⏳" in open(paths["hdr"]).read(), "immediate status missing"
assert open(paths["pushed"]).read().strip() == "x", "in-flight counter missing"

# 2. Background completion updates header + processed counter
time.sleep(3)
assert "⏭" in open(paths["hdr"]).read(), "background success didn't update header"
assert open(paths["processed"]).read().strip() == "x", "processed counter missing"

# 3. Failure rolls back the optimistic hide
open(stub, "w").write('print("not json")\n')
for p in (paths["hdr"], paths["removed"], paths["processed"]):
    open(p, "w").close()
subprocess.run([script, "failing task (5) [10]"], timeout=10)
time.sleep(1.5)
assert "failing task" not in open(paths["removed"]).read(), "failed defer left task hidden"
assert "restored" in open(paths["hdr"]).read(), "failure header missing"

print("PASS: defer is async (instant return, bg completion, failure rollback)")
EOF
