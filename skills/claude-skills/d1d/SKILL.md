---
name: "did"
description: "Mark habits or tasks as done. Supports multiple items separated by comma/semicolon. Writes to 0₦ (habits) or 0分 (Todoist tasks), completes in Todoist. Usage: /did <habit> [time], <habit2> [time2] [yesterday|M/D]"
user-invocable: true
---
/I have 
# Mark Habit Done (/did)

Write to Neon spreadsheet + close Todoist task. AppleScript templates are in `applescript-ref.md` (same directory) — read that file when you need a template.

## Execution Model

**Primary path: `did-fast.py`** — a Python CLI that handles 0₦ habits and Todoist-matched tasks in ~10s via batched AppleScript and parallel Todoist closes. Use it for all /did calls except no-args mode.

```
DID_FAST=~/i446-monorepo/tools/did/did-fast.py
```

### Standard flow (has arguments)

1. **Hook output:** A UserPromptSubmit hook (`did-next-hook.sh`) may output "Next up:" task list. If present, display it verbatim and wait for user's pick (1-N for `/tg`, last number = skip).
2. **Run did-fast.py** (after pick or if no hook output):
   ```bash
   python3 $DID_FAST "<all args verbatim>"
   ```
3. **Parse the JSON output.** Report results from `results[]` to the user. For any items in `agent_needed[]`, fall back to the **agent path** (Steps described below).
4. **Refresh cache** (background, after reporting):
   ```bash
   python3 $DID_FAST --refresh-cache
   ```

### Agent fallback

Only spawn a background agent for items returned in `agent_needed[]`:
- `1n+` matches (need week row calc + 0分 cell reference)
- Time range items (need Toggl entry creation)
- Past date items (need posthoc flow)
- Variable tasks (need domain disambiguation)
- Build order check-offs (need markdown edit)

The agent follows the original Steps -2 through 6b below for these items only.

## No-args Mode

When `/did` is called with **no arguments**:

1. **Read Toggl cache** at `~/.claude/skills/tg/cache.json`. If no timer running, output `No timer running.` and exit.
2. **Stop the Toggl timer** via `python3 ~/i446-monorepo/mcp/toggl_server/toggl_cli.py stop`. Update the tg cache (`running: null`). Parse the stop output to extract duration in minutes.
3. **Check for /do session.** Read `~/.claude/skills/do/active.json`. If it exists and is valid:
   - The task is a variable-point activity started by `/do`.
   - Points = duration (minutes) from step 2 **+ `start_bonus`** from active.json (0 or absent for plain variable tasks; 5 for xk20s/xk22s, 15 for 一起饭).
   - Run `python3 $DID_FAST "<points_task> <points>"` where `points_task` comes from `active.json` (fall back to `task` for old sessions without the field).
   - After success, delete `active.json` to clear the session.
   - Skip to step 4 (do NOT use the raw timer description as input).
4. **Standard path (no /do session).** Use the timer description as the /did input. Strip Toggl-specific prefixes/noise, then run `python3 $DID_FAST "<description>"`. Handle results + agent_needed as in the standard flow.
5. **Surface next tasks.** Read `~/.claude/skills/next/cache.json` and display the top 9 tasks (same format as `/next`). Ask user to pick one (1-9) or skip. If they pick, start a Toggl timer via the CLI and update the tg cache.

This lets the user finish a task and immediately start the next one in a single flow: `/did` → stop timer → mark done → pick next → start timer.

## Parsing (Steps -2 to 0.5)

**Date:** Last token `yesterday` or `M/D` → strip and set `targetDate`. Default: today (M/D format).

**Split:** `,` or `;` → separate items, process each independently.

**Aliases:** `stats m5x2`→`stats m5x2`, `math`→`问学`, `skin2skin`→`问学`

**Cumulative columns:** `问学` — add to existing value instead of overwriting.

**Cumulative 1n+ habits:** `一起饭` — routes to Step 1n but adds +30 per occurrence (not the row 3 value). Uses the cumulative variant of the 1n+ write template.

**@project override:** `@code` token → set `projectOverride`, strip from item.

**Points override:** `[N]` or `{N}` in user input → set `pointsOverride` to N, strip from item. When set, this overrides the `[N]` extracted from the matched Todoist task in Step 5. `{N}` also triggers a 0g bonus write (append +N to 0分 column Z).

**Bonus points:** `+N` in user input → adds N bonus points on top of the computed value (duration or default). Written to 0分 alongside the base points. Example: `/did xk22 1430-1501 +5` → 31 min duration + 5 bonus = 36 points to 0分.

**Time range:** `HHMM-HHMM` pattern → extract start/end, compute duration as `[time]`, set `hasTimeRange`.

## Routing (Step 0)

**IMPORTANT:** Always complete Steps 0.1–0.3 in order before falling through to Step 6. Do NOT short-circuit to Step 6 just because the input starts with a number. Many 1n+ headers start with `1` (e.g. `1 xk88`, `1 i9`, `1 m5x2`). Match the **full input string** against headers first.

1. **0₦ match** (full input = exact column header in 0n row 1, case-insensitive) → today: Steps 1–4. Past date: Step 6b.
2. **1n+ match** (full input = column header in 1n+ row 1, case-insensitive) → Step 1n.
3. **Todoist match** (word overlap ≥0.6 across ALL pages, paginate with `next_cursor`) → Step 5.
4. **No match** → Step 6 (variable task).

Word overlap: tokenize both sides (lowercase, strip `[N]`/`(N)`/stopwords, **strip apostrophes and punctuation** so `mother's`→`mothers`, `don't`→`dont`), ratio = query words found in task / total query words. ≥0.6 matches. Tie: highest ratio. 0.4 only if exactly one task.

## Step 1–4: 0₦ Habit Flow

**1b. Auto-detect time:** No `[time]` provided → check Toggl today for matching entries (description substring or project code match via /tg shortcode mapping). Sum minutes. No match → `1`.

**2. Write to 0₦:** Use "Write to 0₦" template from `applescript-ref.md`. Run via `~/.claude/skills/_lib/ix-osa.sh` (or the `ix_osa.run()` Python helper for the background agent). NEVER call local `osascript` — writes must land on Ix to avoid OneDrive merge conflicts.

**2b. 0l special case:** If habit is `0l`, run "0l completion time" template.

**2c. Verify:** Check `verify=` in AppleScript return. Flag `⚠ checksum mismatch` if wrong.

**3. Close Todoist:** Search `0neon`-labeled tasks using **word overlap matching** (same algorithm as Step 0: tokenize both sides, ratio ≥ 0.6). Also search `夜neon`-labeled tasks (evening habits like `hcmc`). **Dash-normalization:** strip ` - ` from both sides before matching. **Alias expansion:** if the habit was resolved via alias (e.g. `math` → `问学`), search for BOTH the original input AND the alias. Close if found.

**3b. Validation gate:** BLOCKING — confirm Step 3 was attempted. If not → go back. This is the most common failure mode.

**4. Report:** `<habit> → <time> (today) [+ todoist] ✓ verify=<value>`

## Step 5: Todoist-only Task

Task found in Step 0. Extract `[N]` points from the Todoist task. If `pointsOverride` was set during parsing, use that instead. Map labels to 0分 column:
- `i9`/`i447`/`f693`/`f694` → R, `m5x2` → S, `g245`/`infra`/`cc` → T, `hcmc` → U, `hcb`/`hcbp` → W, `xk87`/`xk88` → X, `s897` → Y

Append `+N` to 0分 using "Append to 0分" template (where N = `pointsOverride` if set, else task's `[N]`). Close the Todoist task.

**5b. Build order check-off:** If the closed task has label `关键路径` (or `#0g`/`#-1g`), flip the matching line in `~/vault/g245/build-order.md` from `- [ ]` to `- [x]`:

- Search `## 0₲` section (before `### 以后的目标`), 2-space indent: `  - [ ] <content>` → `  - [x] <content>`
- Then search `## -1₲` section within any 地支 block (寅/卯/辰/…), 4-space indent: `    - [ ] <content>` → `    - [x] <content>`

Match the line whose bullet content equals the Todoist task content (preserve `(N)`, `[N]`, `{N}` annotations — they're written identically by `/0g` and `/-1g`). If no exact match, fall back to substring match on the bare goal text. If still no match, skip silently — don't fail the /did flow.

**5.5.** If `hasTimeRange`, create Toggl entry via `toggl_create_entry`.

## Step 6: Variable Task

No 0₦ or Todoist match. Number = **points** not minutes.

1. Infer domain (or use `projectOverride`): social→s897(AH), family→xk87(AG), health→hcb(AF), work→m5x2(AB), tech→i9(AA), media→hcmc(AD), goals→g245(AC). Ambiguous → ask.
2. Append points to 0分.
3. Create posthoc Todoist task: `content + " @posthoc @YYYY-MM-DD"`, labels `["posthoc", "<domain>"]`, due `targetDate`. Immediately close it.

## Step 1n: 1neon Task

Matches 1n+ sheet header. Do NOT write to 0₦.

1. Find column + week row (M.W = month.ceil(day/7)). Read points from row 3. Write points to cell. Use "1n+ write" template.
   - **Cumulative 1n+ habits** (e.g. `一起饭`): Instead of writing the row 3 value, **add the fixed increment** to the existing cell value (use the cumulative variant of the 1n+ write template). Fixed increments: `一起饭` = 30.
   - **Variable 1n+ habits** (`s897`, `family`/`家`, `relax`, `s+hcbp`): Instead of reading row 3, **add the user-provided value** (trailing number or `[N]`) to the existing cell value. These are input-based tasks where points = minutes. If no value provided, the task needs a value (from `/do` timer or explicit input). Write points directly to 0分 (not as cell reference) to avoid over-counting on repeated weekly use.
2. Append cell reference `+'1n+'!{col}{weekRow}` to 0分 (non-variable tasks only). Map column via `g245/1-neon-meta.md`. Use "1n+ to 0分" template. For 一起饭 → 0分 column AG (xk). For variable tasks, append `+N` directly to 0分 instead.
3. Search `1neon`-labeled Todoist tasks. Close if found. Error if not found (but still complete steps 1-2).

**1n+ aliases:** `家` → `family`, `relax` → `relax {60}`

## Step 6b: Posthoc Habit

0₦ match + past date. No Neon write. Create posthoc Todoist task (same as Step 6.3 but with labels `["posthoc", "0neon"]`).

## Cache & Tracking

**Task queue:** `~/vault/z_ibx/task-queue.json` — `{refreshed, tasks: [{id, content, cat, dueDate}]}`. Categories: `0n` (0neon), `1n` (1neon), `0g` (关键路径).

**Completed-today:** `~/vault/z_ibx/completed-today.json` — `{date, names: [...]}`. Background agent appends completed habit name (lowercase). Date gate: reset on new day.

**Cache refresh** (background agent, after /did work): Query Todoist for 0neon + 1neon + 关键路径 tasks (3 parallel calls, limit 50 each). Build `{id, content, cat, dueDate}` list sorted 0n→1n→0g. Write cache. Update completed-today.

**Next-task script:** `~/i446-monorepo/tools/did/next-task.py <habit>` — reads cache + completed-today, filters to today/overdue, excludes completed, shows top 5. Hook runs this automatically.

## Notes

- Excel must be open with `Neon分v12.2.xlsx` **on Ix**. All writes
  go through `~/.claude/skills/_lib/ix-osa.{sh,py}`. If Ix is
  unreachable, the helper exits 3 and the /did step hard-fails — do
  NOT write locally (would cause OneDrive merge conflicts).
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
| `/did stats m5x2` — Todoist is "m5x2 stats (4) [8]" | Step 3 word-overlap matches despite word order | Must NOT skip Todoist close |
| `/did math` — alias→问学, Todoist is "math with kids (60) [70]" | Step 3 searches original "math" too, word-overlap matches | Must NOT skip Todoist close |
| `/did hiit` then `/did 0l` | completed-today filters hiit from suggestions | Must NOT re-suggest completed recurring tasks |
| `/did 1 xk88` — 1n+ header is "1 xk88" | Step 0.2 matches 1n+ header → Step 1n, closes 1neon Todoist task | Must NOT treat leading "1" as points and route to Step 6 |
| `/did 1 i9` — 1n+ header is "1 i9" | Step 0.2 matches 1n+ header → Step 1n | Must NOT route to Step 6 as "1 point to i9" |
| `/did PTC` — Todoist match "PTC feedback [180]" with label xk87 | Step 5: extract [180] from task, write +180 to 0分 AG | Must NOT use 0 pts just because user input had no [N] — always extract from the matched Todoist task |
| `/did xk22 1430-1501 +5` — 0₦ variable habit with bonus | Step 0.1: write 31 to 0₦, write 36 (31+5) to 0分 X | Must NOT ignore +5 bonus; must NOT treat +5 as the time value |
