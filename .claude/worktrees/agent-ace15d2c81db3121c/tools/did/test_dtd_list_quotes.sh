#!/bin/bash
# Regression: the DTD_LIST generator embeds python inside a zsh DOUBLE-quoted
# string (python3 -c "..."). Any double quote in that python terminates the -c
# argument at runtime, mangling the code — dtd then rendered an
# IndentationError traceback as the fzf task list. A comment containing
# "later today" (with quotes) triggered exactly this.
set -e
SCRIPT="$HOME/i446-monorepo/tools/did/dtd.sh"

python3 - "$SCRIPT" <<'EOF'
import json
import os
import re
import subprocess
import sys
import tempfile

src = open(sys.argv[1]).read()
m = re.search(r"cat > \"\$DTD_LIST\" << 'LISTEOF'\n(.*?)\nLISTEOF", src, re.S)
assert m, "DTD_LIST heredoc not found in dtd.sh"
pm = re.search(r'python3 -c "\n(.*?)\n" "\$1"', m.group(1), re.S)
assert pm, "embedded python3 -c block not found in DTD_LIST"
py = pm.group(1)

# 1. No double quotes anywhere in the embedded python — they end the zsh string.
bad = [i + 1 for i, l in enumerate(py.split("\n")) if '"' in l]
assert not bad, (
    f"double quote(s) inside the zsh double-quoted python -c body at "
    f"line(s) {bad} — these truncate the script at runtime")

# 2. It compiles.
compile(py, "<dtd-list>", "exec")

# 3. It executes end-to-end against a dummy cache and emits the task.
tmp = tempfile.mkdtemp()
paths = {n: os.path.join(tmp, n) for n in ("cache.json", "done.json", "removed", "skipped")}
json.dump({"today": [{"id": "1", "content": "test task (5) [10]",
                      "labels": ["i9"], "due": "2020-01-01", "recurring": False}]},
          open(paths["cache.json"], "w"))
json.dump([], open(paths["done.json"], "w"))
open(paths["removed"], "w").close()
open(paths["skipped"], "w").close()
r = subprocess.run(
    ["python3", "-c", py, paths["cache.json"], paths["done.json"],
     paths["removed"], "2020-01-01", "80", paths["skipped"]],
    capture_output=True, text=True, timeout=15)
assert r.returncode == 0, f"list script crashed: {r.stderr[:300]}"
assert "test task" in r.stdout, f"task missing from output: {r.stdout[:200]!r}"
print("PASS: DTD_LIST embedded python is quote-safe, compiles, and executes")
EOF
