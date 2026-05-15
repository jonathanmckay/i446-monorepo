# Feature: dtd start mode + current timer display + task ordering

## Summary

Extend dtd.sh so it can both "complete" and "start" tasks from the same fzf picker. Add a persistent 1-2 line header showing the current Toggl timer. Order tasks by priority tier (0n > 1n > 0g > P1 > P2 > P3 > rest).

## Design

### Approach

**1. Start mode via `>` prefix (escape character)**

When the user types `>` as the first character of their fzf query, the selection switches from "complete this task" to "start a Toggl timer for this task." The `>` prefix is natural: it means "go do this next." It won't conflict with any task name.

Implementation: after fzf returns a selection, check if the user's query started with `>`. fzf's `--print-query` flag returns the typed query as the first line of output. If query starts with `>`, route to "start" instead of "did."

Start action:
- Strip annotations from the task name
- Resolve the Toggl project using the same SHORTCODES table from tg-fast.py (or HABIT_PROJECT from did-fast.py)
- Stop any running timer via `toggl_cli.py stop`
- Start a new timer via `toggl_cli.py start <desc> <project>`
- Do NOT mark the task as done
- Do NOT feed it to the background worker
- Show "Started: <desc> -> <project>" in the fzf header

**2. Current timer in fzf header**

Before entering the fzf loop, fetch the current Toggl timer once via `toggl_cli.py current`. Display it as 1-2 lines at the top of the fzf header. Refresh each loop iteration (cheap: local CLI call, ~200ms).

Header format:
```
  ▶ <desc> @<project> (Xm)
  ✓ last: <status>
```

Line 1 = current timer (or "no timer" if idle). Line 2 = last completion status from the background worker (existing behavior, just moved to line 2).

**3. Task ordering**

The jq filter that builds the fzf list already reads from cache sections: `0neon`, `1neon`, `夜neon`, `关键路径`, and `today`. Instead of flattening all sections equally and deduping, we emit them in priority order and let the dedup pass preserve first-seen position:

```
0neon (0n habits)          # daily habits come first
1neon (1n+ weeklies)       # then weekly tasks
関键路径 (critical path)   # then 0g/critical-path tasks
today P1                   # then by Todoist priority
today P2
today P3
today P4
```

This is purely a jq rewrite. No API calls, no concurrency concerns. The cache is already snapshotted once at startup (line 31 of dtd.sh). We just change the *order* of concatenation and add a priority sort within the `today` bucket.

The Todoist task objects in cache include priority info (embedded in labels or content). For the `today` bucket, we'll parse `(N)` time estimates and `[N]` point values, but the primary sort is by Todoist priority which is encoded in the task's `priority` field if present, or inferred from labels like `p1`, `p2`, etc.

**Note on concurrency:** No new API calls are introduced. The cache snapshot is read once (existing behavior). The `toggl_cli.py current` call is a single local HTTP request to Toggl's API, not to Todoist. No race conditions possible.

### Files to change

- `tools/did/dtd.sh` -- add `--print-query` to fzf, branch on `>` prefix, add timer header, reorder jq

### Files to NOT change

- `tools/did/did-fast.py` -- completion pipeline is untouched
- `tools/did/mark-completed.py` -- no changes needed
- `tools/did/next-task.py` -- separate tool, not involved
- `tools/tg/tg-fast.py` -- we call toggl_cli.py directly, no need to route through tg-fast
- `mcp/toggl_server/toggl_cli.py` -- already has `start`, `stop`, `current` commands

## Implementation steps

1. **Task ordering (jq rewrite)** -- `dtd.sh` lines 93-110
   - Emit `0neon` first, then `1neon`, then `関键路径`, then `today` sorted by priority
   - Priority sort within `today`: parse Todoist priority field (p1=4, p2=3, p3=2, p4=1 in API, but we invert for display). If tasks have a numeric priority field, sort descending. If not, default to lowest.
   - Dedup by `.id` preserving first-seen order

2. **Current timer header** -- `dtd.sh` before fzf loop and inside loop
   - Before the loop: `TOGGL_CURRENT=$(python3 "$TOGGL_CLI" current 2>/dev/null)`
   - Parse into 1-line format: `▶ <desc> @<project> (Xm)` or `▶ (idle)`
   - Pass as first line of fzf `--header`
   - Refresh at top of each loop iteration

3. **Start mode via `>` prefix** -- `dtd.sh` after fzf returns
   - Add `--print-query` to fzf call
   - Read query (line 1) and selection (line 2) from fzf output
   - If query starts with `>`:
     - Strip annotations from selection
     - Look up project from SHORTCODES (inline a small lookup table in bash, or call a tiny Python helper)
     - `python3 "$TOGGL_CLI" stop` then `python3 "$TOGGL_CLI" start "$clean" "$project"`
     - Update header: "Started: $clean -> $project"
     - Do NOT add to session_done or send to worker
   - Else: existing "did" flow unchanged

## Test plan

- [ ] Run `dtd.sh`, verify tasks appear in order: 0n habits first, then 1n, then critical path, then today by priority
- [ ] Type `>` then select a task: verify timer starts, task is NOT marked done
- [ ] Verify the header shows the current running timer
- [ ] Complete a task (no `>` prefix): verify existing behavior unchanged
- [ ] Edge: select with `>` when no timer is running: verify it starts cleanly
- [ ] Edge: `>` start when another timer is running: verify old timer stops first
- [ ] Perf: verify fzf opens in <1s (no new API calls in hot path)

## Risks / open questions

- ~~**Project resolution in bash**~~ Resolved: added `--resolve` to tg-fast.py (3 lines).
- ~~**Priority field in cache**~~ Resolved: added `priority` to both `fetch_label` and `fetch_today` in did-fast.py. Todoist API returns numeric priority (4=urgent, 1=normal); jq sorts by negated value.
- **Note on `group_by` vs `reduce`**: jq's `group_by(.id)` sorts groups alphabetically by id, destroying our carefully constructed priority order. Replaced with `reduce` that preserves insertion order. Tested at 9ms for 85 tasks.

## Result
- **Status:** Complete
- **Tests:** jq filter tested with live cache (85 tasks, 9ms); `--resolve` tested for known/unknown shortcodes; priority ordering verified visually
- **Changed files:** 3 (dtd.sh, did-fast.py, tg-fast.py)
- **Notes:** Todoist priority is numeric (4=highest), not string "p1". Adjusted jq sort accordingly. The `reduce` dedup is O(n^2) but 85 items runs in <10ms so irrelevant.
