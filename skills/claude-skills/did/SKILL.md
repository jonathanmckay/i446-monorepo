---
name: "did"
description: "Mark habits or tasks as done. Supports multiple items separated by comma/semicolon. Writes to 0₦ (habits) or 0分 (Todoist tasks), completes in Todoist. Usage: /did <habit> [time], <habit2> [time2] [yesterday|M/D]"
user-invocable: true
---

# Mark Habit Done (/did)

Write to Neon spreadsheet + close Todoist task. AppleScript templates are in `applescript-ref.md` (same directory) — read that file when you need a template.

## Execution Model

A **UserPromptSubmit hook** (`did-next-hook.sh`) runs BEFORE Claude processes the prompt. If the prompt starts with `/did`, the hook outputs a "Next up" task list from the local cache. This output appears in a system-reminder tag.

When you see hook output containing "Next up:" and "Pick [1-N]:", do this:

1. **Display the hook output verbatim** to the user. Do not re-run the script.
2. **Wait for user's pick.** If they pick 1–5, run `/tg` for that task (strip `[N]`, `(N)`, suffixes like `- Daily 分`). If they pick the last number (skip), do nothing.
3. **After the pick** (or if no hook output), launch the background agent for the /did pipeline.
4. **Background** (Agent with `run_in_background: true`): Run Steps -2 through 6b (whichever path applies), then refresh cache + update completed-today. Report results when done.

## Parsing (Steps -2 to 0.5)

**Date:** Last token `yesterday` or `M/D` → strip and set `targetDate`. Default: today (M/D format).

**Split:** `,` or `;` → separate items, process each independently.

**Aliases:** `hcmc`→`night hcmc`, `stats m5x2`→`stats m5x2`, `math`→`问学`, `skin2skin`→`问学`

**Cumulative columns:** `问学` — add to existing value instead of overwriting.

**@project override:** `@code` token → set `projectOverride`, strip from item.

**Time range:** `HHMM-HHMM` pattern → extract start/end, compute duration as `[time]`, set `hasTimeRange`.

## Routing (Step 0)

1. **0₦ match** (exact column header in row 1) → today: Steps 1–4. Past date: Step 6b.
2. **1n+ match** (column header, case-insensitive) → Step 1n.
3. **Todoist match** (word overlap ≥0.6 across ALL pages, paginate with `next_cursor`) → Step 5.
4. **No match** → Step 6 (variable task).

Word overlap: tokenize both sides (lowercase, strip `[N]`/stopwords), ratio = query words found in task / total query words. ≥0.6 matches. Tie: highest ratio. 0.4 only if exactly one task.

## Step 1–4: 0₦ Habit Flow

**1b. Auto-detect time:** No `[time]` provided → check Toggl today for matching entries (description substring or project code match via /tg shortcode mapping). Sum minutes. No match → `1`.

**2. Write to 0₦:** Use "Write to 0₦" template from `applescript-ref.md`. Run via `osascript -e '...'`.

**2b. 0l special case:** If habit is `0l`, run "0l completion time" template.

**2c. Verify:** Check `verify=` in AppleScript return. Flag `⚠ checksum mismatch` if wrong.

**3. Close Todoist:** Search `0neon`-labeled tasks for content match (case-insensitive substring). **Dash-normalization:** strip ` - ` from both sides before matching. Close if found.

**3b. Validation gate:** BLOCKING — confirm Step 3 was attempted. If not → go back. This is the most common failure mode.

**4. Report:** `<habit> → <time> (today) [+ todoist] ✓ verify=<value>`

## Step 5: Todoist-only Task

Task found in Step 0. Extract `[N]` points. Map labels to 0分 column:
- `i9`/`i447`/`f693`/`f694` → AA, `m5x2` → AB, `g245`/`infra`/`cc` → AC, `hcmc` → AD, `xk87`/`xk88` → AG, `s897` → AH, `hcb`/`hcbp` → AF

Append `+N` to 0分 using "Append to 0分" template. Close the Todoist task.

**5.5.** If `hasTimeRange`, create Toggl entry via `toggl_create_entry`.

## Step 6: Variable Task

No 0₦ or Todoist match. Number = **points** not minutes.

1. Infer domain (or use `projectOverride`): social→s897(AH), family→xk87(AG), health→hcb(AF), work→m5x2(AB), tech→i9(AA), media→hcmc(AD), goals→g245(AC). Ambiguous → ask.
2. Append points to 0分.
3. Create posthoc Todoist task: `content + " @posthoc @YYYY-MM-DD"`, labels `["posthoc", "<domain>"]`, due `targetDate`. Immediately close it.

## Step 1n: 1neon Task

Matches 1n+ sheet header. Do NOT write to 0₦.

1. Find column + week row (M.W = month.ceil(day/7)). Read points from row 3. Write points to cell. Use "1n+ write" template.
2. Append cell reference `+'1n+'!{col}{weekRow}` to 0分. Map column via `g245/1-neon-meta.md`. Use "1n+ → 0分" template.
3. Search `1neon`-labeled Todoist tasks. Close if found. Error if not found (but still complete steps 1–2).

## Step 6b: Posthoc Habit

0₦ match + past date. No Neon write. Create posthoc Todoist task (same as Step 6.3 but with labels `["posthoc", "0neon"]`).

## Cache & Tracking

**Task queue:** `~/vault/z_ibx/task-queue.json` — `{refreshed, tasks: [{id, content, cat, dueDate}]}`. Categories: `0n` (0neon), `1n` (1neon), `0g` (关键径路).

**Completed-today:** `~/vault/z_ibx/completed-today.json` — `{date, names: [...]}`. Background agent appends completed habit name (lowercase). Date gate: reset on new day.

**Cache refresh** (background agent, after /did work): Query Todoist for 0neon + 1neon + 关键径路 tasks (3 parallel calls, limit 50 each). Build `{id, content, cat, dueDate}` list sorted 0n→1n→0g. Write cache. Update completed-today.

**Next-task script:** `~/i446-monorepo/tools/did/next-task.py <habit>` — reads cache + completed-today, filters to today/overdue, excludes completed, shows top 5. Hook runs this automatically.

## Notes

- Excel must be open with `Neon分v12.2.xlsx`.
- AppleScript calls must be **sequential** (race condition on concurrent writes).
- Column headers in row 1, exact match. Date in col C, M/D format.

## Regression tests

| Input | Expected | Must NOT happen |
|-------|----------|-----------------|
| `/did 0g 2` — 0₦ match | Steps 1–4, writes to 0₦ | Must NOT search all Todoist tasks |
| `/did 0l 2 4/1` — past date | Step 6b posthoc | Must NOT write to 0₦ |
| `/did ibx - s897` — Todoist is "ibx s897 [6]" | Step 3 dash-norm matches | Must NOT skip Todoist close |
| `/did ibx i9` — Todoist is "ibx - i9 [20]" | Step 3 dash-norm matches | Must NOT skip Todoist close |
| `/did 30m session with lx` — Todoist has "30m lx session [30]" | Step 0 word-overlap → Step 5 | Must NOT create posthoc duplicate |
| `/did hiit` then `/did 0l` | completed-today filters hiit from suggestions | Must NOT re-suggest completed recurring tasks |
