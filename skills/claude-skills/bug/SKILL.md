---
name: "bug"
description: "Investigate the current bug, implement a fix, then add a regression test to prevent it coming back."
user-invocable: true
---

# Fix This

Investigate the problem described in the conversation, fix it, and add a regression test.

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

Run the test file to confirm the new test passes and no existing tests broke:

```bash
python3 -m pytest <test_file> -v
```

Report: what was broken, what line was changed, and that tests pass.
