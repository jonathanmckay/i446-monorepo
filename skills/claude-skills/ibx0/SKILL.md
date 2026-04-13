---
name: "ibx0"
description: "Mark all inbox habits as done (ibx s897, ibx i9, slack github, slack m5x2, ibx m5x2, teams). Usage: /ibx0"
user-invocable: true
---

# Mark Inbox Habits Done (/ibx0)

Batch-marks all six inbox-related habits as done via `/did`, which writes to 0₦ (Neon) and completes matching Todoist tasks.

## Usage

```
/ibx0
```

No arguments. Marks all six items for today.

## Steps

### Step 1: Run /did with all six items

Execute exactly this:

```
/did ibx - s897, ibx i9, slack github, slack m5x2, ibx m5x2, teams
```

This delegates to the `/did` skill, which handles:
- Writing `1` to each habit's column in the 0₦ sheet
- Completing the matching `0neon`-labeled Todoist task for each

### Step 2: Report

Show the combined `/did` output for all six items. No additional commentary needed.
