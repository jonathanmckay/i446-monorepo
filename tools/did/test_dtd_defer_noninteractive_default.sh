#!/bin/bash
# Regression (2026-08-09): "why don't I see 1s in my task list?" — the
# weekly (1neon) recurring habit "1s" silently vanished for a week with no
# dated stand-in, no warning, and no trace beyond a 0-point closed "deferred:
# ... -> next occurrence ..." posthoc. neon-task-checksum.py can't catch this
# class of bug at all (it only checks a matching card EXISTS, never its due
# date), which is why the checksum reported "weekly: present" the whole time.
#
# Root cause: dtd.sh's ctrl-d defer wrapper only prompts for a defer target
# when DTD_DEFER_PROMPT is set AND a live /dev/tty is readable (dtd's own
# comment: "the test harness and any scripted caller run the script with the
# flag unset"). When no prompt is shown at all, the OLD code still forced
# days="auto" unconditionally — indistinguishable from a live human
# deliberately leaving the prompt blank (which IS meant to skip the
# occurrence with no copy, per defer-fast.py's own documented "0"/"auto"
# sentinel). A scripted/automated caller (an agent driving dtd non-
# interactively) never got to make that choice, yet got the destructive
# "skip, no dated copy" behavior anyway. defer-fast.py's OWN default when
# simply given no target ("" or omitted) is the safe one: +1 day, WITH a
# dated one-off copy — dtd.sh's rewrite was overriding that safe default for
# every non-interactive caller, silently, for every recurring habit
# (daily or weekly) they ever deferred.
set -e
cd "$(dirname "$0")"

python3 - <<'EOF'
import re, subprocess, tempfile, os

src = open("dtd.sh").read()
m = re.search(r'cat > "\$DTD_DEFER" << DEFEREOF\n(.*?)\nDEFEREOF', src, re.S)
assert m, "DTD_DEFER heredoc not found"
body = m.group(1)

tmp = tempfile.mkdtemp()
paths = {k: os.path.join(tmp, k) for k in ("hdr", "removed", "pushed", "processed", "journal")}
for p in paths.values():
    open(p, "w").close()
open(paths["removed"] + ".ids", "w").close()

body = body.replace("$DTD_HDR", paths["hdr"]).replace("$DTD_REMOVED", paths["removed"])
body = body.replace("$DTD_PUSHED", paths["pushed"]).replace("$DTD_PROCESSED", paths["processed"])
body = body.replace("$UNDO_FAST", "/usr/bin/true").replace("$DTD_JOURNAL", paths["journal"])
cache_stub = os.path.join(tmp, "cache.json")
open(cache_stub, "w").write("{}")
body = body.replace("$DTD_RESOLVE", os.path.join(os.getcwd(), "dtd_resolve.py"))
body = body.replace("$DTD_CACHE_FILE", cache_stub)
body = body.replace("\\$", "$")

# Stub defer-fast.py: record the exact argv it was invoked with (specifically
# whether "auto" ever reaches it) instead of doing any real Todoist work.
argv_log = os.path.join(tmp, "argv.log")
stub = os.path.join(tmp, "defer_stub.py")
open(stub, "w").write(
    "import sys, json\n"
    f"open({argv_log!r}, 'w').write(repr(sys.argv))\n"
    'print(json.dumps({"target_date": "2026-08-16", "claimed_points": 2, "remaining_points": 43}))\n'
)
body = body.replace('DEFER_FAST="$HOME/i446-monorepo/tools/did/defer-fast.py"',
                    f'DEFER_FAST="{stub}"')
script = os.path.join(tmp, "defer.sh")
open(script, "w").write(body)
os.chmod(script, 0o755)

# Run exactly as "the test harness and any scripted caller" do per dtd.sh's
# own comment: DTD_DEFER_PROMPT unset. subprocess.run with no stdin/pty also
# means /dev/tty is not a live prompt target either way.
env = {k: v for k, v in os.environ.items() if k != "DTD_DEFER_PROMPT"}
subprocess.run([script, "1s-task-id"], timeout=10, env=env)

import time
time.sleep(1.5)  # the network round trip runs detached; give it a beat

assert os.path.exists(argv_log), "defer-fast.py stub was never invoked"
argv = eval(open(argv_log).read())
days_arg = argv[-1]
assert days_arg != "auto", (
    f"non-interactive caller got 'auto' (skip occurrence, NO dated copy) "
    f"forced on it without ever being asked — argv={argv!r}. This is exactly "
    f"the '1s vanished for a week' bug: a scripted/agent-driven defer must "
    f"fall through to defer-fast.py's own safe default (+1 day, dated copy "
    f"created), not dtd.sh's blank-prompt-means-skip sentinel, which is only "
    f"valid when a live human actually saw the prompt and chose it."
)
assert days_arg == "", f"expected an empty (pass-through) defer target, got {days_arg!r}"

print("PASS: non-interactive/scripted defer never gets the 'auto' skip sentinel forced on it")
EOF
