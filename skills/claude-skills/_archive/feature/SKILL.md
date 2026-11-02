---
name: "feature"
description: "Plan, implement, and test a new feature. Saves a plan doc, implements it, then runs tests. Usage: /feature <description>"
user-invocable: true
---

# Feature (/feature)

Plan a feature, save the plan as a markdown file, implement it, and verify with tests.

## Arguments

The user message after `/feature` describes the feature they want. Can be a sentence, a paragraph, or bullet points.

Examples:
- `/feature add a /reviews page to the Hugo site that groups by genre`
- `/feature slack integration for ibx — poll channels, show unread in the card TUI`
- `/feature toggl CLI should support editing existing entries`

## Steps

### Step 1: Identify the repo and scope

Determine which repo/project the feature belongs to by examining:
- The current working directory
- The feature description (mentions of tools, files, or systems)
- Recent conversation context

Run `git rev-parse --show-toplevel` to confirm the repo root. If unclear, ask the user.

### Step 2: Research

Before planning, understand the codebase:
- Read relevant source files to understand existing patterns, conventions, and architecture
- Check for existing tests to understand the testing approach
- Look for related features that set precedent for how this should be built
- Identify files that will need to change

Spend real time here. A plan based on assumptions will waste more time than a plan based on reading.

### Step 3: Write the plan

Create a markdown plan file at `<repo_root>/plans/<date>-<slug>.md`:

```markdown
# Feature: <title>

## Summary
<1-2 sentence description of what this feature does and why>

## Design

### Approach
<How this will be implemented — the key decisions and tradeoffs>

### Files to change
- `path/to/file.ext` — <what changes>
- `path/to/new-file.ext` — <new file, purpose>

### Files to NOT change
<Explicitly list files/areas that are out of scope to prevent drift>

## Implementation steps
1. <step> — <which file(s)>
2. <step> — <which file(s)>
3. ...

## Test plan
- [ ] <test case description>
- [ ] <test case description>
- [ ] <edge case>

## Risks / open questions
- <anything uncertain>
```

Create the `plans/` directory if it doesn't exist.

### Step 4: Present the plan

Show the plan to the user. Set terminal to green (plan approval needed):

```bash
~/i446-monorepo/scripts/term-color.sh green
```

Then start a 15-second countdown that defaults to proceeding:

```
Plan saved. Proceeding in 15s unless you intervene...
```

Use `sleep 15` (or the ScheduleWakeup tool if in /loop mode). If the user sends any message during the countdown:
- **Feedback** → revise the plan, re-present, restart the countdown
- **"n" / "no" / "stop"** → abort implementation, keep the plan file

If the countdown completes with no user input, proceed to Step 5 automatically.

### Step 5: Implement

Follow the plan step by step. For each implementation step:
1. Make the changes described in the plan
2. If you discover the plan needs adjustment mid-implementation, note it but keep going unless it's a blocking issue
3. Prefer small, focused edits over large rewrites

### Step 6: Run existing tests

Before writing new tests, run the existing test suite to make sure nothing broke:

```bash
# Detect test runner
if [ -f pytest.ini ] || [ -f pyproject.toml ]; then
    python3 -m pytest -x -q
elif [ -f package.json ]; then
    npm test
elif [ -f go.mod ]; then
    go test ./...
fi
```

If existing tests fail, fix the regression before proceeding.

### Step 7: Write new tests

Add tests for the new feature following the repo's existing test patterns:
- Test the happy path
- Test edge cases identified in the plan
- Test integration with existing code where applicable

### Step 8: Run all tests

Run the full test suite one more time to confirm everything passes:
- New tests pass
- Existing tests still pass

### Step 9: Update the plan

Mark completed steps in the plan file. Add a `## Result` section:

```markdown
## Result
- **Status:** Complete
- **Tests:** N new tests, all passing
- **Notes:** <any deviations from the plan>
```

### Step 10: Report

```
Feature complete: <title>
  Plan: plans/<date>-<slug>.md
  Changed: N files
  Tests: N new, N total passing
```

## Response Style

Verbose during planning (Step 3-4). Minimal during implementation (Step 5-8). One-line summary at the end.
