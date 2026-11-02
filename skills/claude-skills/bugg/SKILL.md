---
name: "bugg"
description: "Investigate the current bug, implement a fix, then add a regression test to prevent it coming back."
user-invocable: true
---

# Fix This

Investigate the problem described in the conversation, fix it, and add a regression test.

## Arguments

The user message after `/bug` contains:
1. **Bug description** — what's broken (required)
2. **Process name** — optional, appended after the description. If present, kill that process before fixing, then restart it after tests pass.

Examples:
- `/bug emails not showing in ibx` — just fix, no process management
- `/bug emails not showing in ibx ibx0` — kill ibx0 process, fix, test, restart ibx0

### Process management

If a process name is given (second argument):

**Before fixing (in parallel with Step 1–2):**
1. Find the running process: `ps aux | grep <process_name> | grep -v grep`
2. Kill it using its PID: `kill <PID>`

**After tests pass (Step 5):**
1. Restart the process using cmux:
   ```bash
   cmux respawn-pane --surface surface:<N> --command "<restart_command>"
   ```
2. Use the process name to determine the restart command:
   - `ibx0` → `bash ~/i446-monorepo/tools/ibx/ibx0_wrapper.sh`

If cmux isn't available or the surface number is unknown, just tell the user to restart manually.

## Steps

### Step 1: Understand the problem

Read the recent conversation to identify:
- What behavior the user observed (the symptom)
- What file/tool/function is misbehaving
- Any error messages or misleading output

### Step 2: Find the root cause

Read the relevant source files. Trace the logic until you find the exact line(s) causing the wrong behavior. Do not guess — read the code.

### Step 3: Fix it

Make the minimal change needed to correct the behavior. Do not refactor surrounding code or add unrelated improvements.

### Step 4: Add a regression test

Open (or create) the test file for the affected module. Add a focused test that:
- Is named to describe the bug (e.g. `test_fetch_inbox_includes_read_emails`)
- Fails on the broken code and passes on the fix
- Prefers AST inspection for structural bugs (wrong flag values, wrong variable names) over mocking, when it makes the intent clearer

### Step 5: Run the tests

Run **only the affected test file**, not the whole suite, and bound it — a
full-repo `pytest` or one hanging test (network, server spin-up) blows the
Bash 120s limit and surfaces as "/bug timed out when finishing":

```bash
python3 -m pytest <test_file> -q --timeout=60 2>/dev/null || python3 -m pytest <test_file> -q
```

(The `--timeout` flag needs `pytest-timeout`; the `||` falls back if it isn't
installed. If you must run a broader check, pass the Bash tool an explicit
`timeout` and `run_in_background` rather than blocking the turn.)

Report: what was broken, what line was changed, and that tests pass.

### Step 6: Restart process (if process name was given)

Restart it now that the fix is verified — **non-blocking**. The timeout people
hit at finish is almost always a foreground service start that never returns
(`python3 server.py` holds the Bash call open until the 120s limit).

- A **launchd** service (dtd, janus, dashboard, …): `launchctl kickstart -k
  gui/$(id -u)/<label>` — returns immediately. Find the label with
  `launchctl list | grep <name>`.
- A **cmux-pane** process (ibx0, …): `cmux respawn-pane --surface
  surface:<N> --command "<restart_command>"`.
- Anything else: background it (`nohup <cmd> >/tmp/<name>.log 2>&1 &`), never
  foreground. Verify it came up with a quick bounded `curl`/`pgrep`; do **not**
  wait on the process itself.

If cmux isn't available or the surface/label is unknown, tell the user to
restart manually rather than blocking.
