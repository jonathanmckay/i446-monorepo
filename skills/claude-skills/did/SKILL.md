---
name: "did"
description: "Mark habits or tasks as done. Supports multiple items separated by comma/semicolon. Writes to 0₦ (habits) or 0分 (Todoist tasks), completes in Todoist. Usage: /did <habit> [time], <habit2> [time2] [yesterday|M/D]"
user-invocable: true
---

# Mark Habit Done (/did)

Write to Neon spreadsheet + close Todoist task. AppleScript templates are in `applescript-ref.md` (same directory) — read that file when you need a template.

## Excel host

All Excel writes go through **Ix** (Mac Mini, Tailscale) — Neon is open there, not on Straylight.

**Preferred path:** the `neon.excel` Python client at `~/i446-monorepo/lib/neon/excel.py`, which talks to the `excel-http` daemon on ix (`localhost:9876`). Cuts per-lookup latency from ~2s to ~30ms. The client falls back to `ssh ix osascript` automatically if the daemon is down.

```python
import sys; sys.path.insert(0, "/Users/mckay/i446-monorepo/lib")
from neon import excel, cols
excel.append("0分", cols.domain_col("0分", "i9"), date="4/29", value="+10")
```

**Fallback / one-shot path:** wrap raw AppleScript in:

```bash
ssh ix 'osascript <<APPLESCRIPT
tell application "Microsoft Excel"
  ...
end tell
APPLESCRIPT'
```

**Column letters:** never hard-code. `~/i446-monorepo/config/neon-cols.json` is the source of truth (regen via `~/i446-monorepo/scripts/regen-neon-cols.py`). Use `cols.col(sheet, header)` or `cols.domain_col(sheet, domain)`.

If `ssh ix` fails (timeout/unreachable), warn the user — this is a degraded state. Set terminal orange via `~/i446-monorepo/scripts/term-color.sh orange`.

## Execution Model

**Fast path (registry-resolved 0n / 1n+ habits):** run the runner directly, no agent. Most /did calls hit this path:

```bash
python3 ~/i446-monorepo/tools/did/run.py "<input>"
```

The runner:
1. Calls `route.py` for routing decision
2. If `step == 0n` or `1n+`: writes Neon, closes Todoist, optionally creates Toggl entry (for `HHMM-HHMM` time ranges), updates `completed-today.json`, prints one-line confirmation. Exit code 0.
3. If `step == unknown`: exits with code 2 (defer to agent)

**Latency:** ~3-5s per call (was 60-90s when agent-mediated). Most of that is Excel writes.

**Slow path (one-off Todoist tasks, variable tasks):** when `run.py` exits with code 2, dispatch the background agent for Step 5 (Todoist word-overlap match) or Step 6 (variable). The agent is needed here because matching a free-form input against the full Todoist tree requires the LLM.

A **UserPromptSubmit hook** (`did-next-hook.sh`) runs BEFORE Claude processes the prompt. If the prompt starts with `/did`, the hook outputs a "Next up" task list from the local cache. This output appears in a system-reminder tag.

When you see hook output containing "Next up:" and "Pick [1-N]:", do this:

1. **Display the hook output verbatim** to the user. Do not re-run the script.
2. **Wait for user's pick.** If they pick 1–5, run `/tg` for that task (strip `[N]`, `(N)`, suffixes like `- Daily 分`). If they pick the last number (skip), do nothing.
3. **After the pick** (or if no hook output), invoke `run.py` directly (fast path). If it exits 2, fall back to launching the background agent.
4. **Background agent** (only when run.py defers): handle Step 5 (Todoist match) or Step 6 (variable task), then refresh cache + update completed-today. Report results when done.

### Next-up suppression (anti-wallpaper rule)

The Next-up panel is wallpaper after the first /did of a session — the same 9 tasks repeat until the user picks one. To save scroll:

- The **background agent** still refreshes the cache every time (`completed-today.json` and `task-queue.json` must stay current).
- In your **user-facing reply**, only render the Next-up list when:
  - It's the first `/did` since you last sent the user a Next-up panel in this conversation, OR
  - The list materially changed (a task that was on it dropped off, or a higher-priority task surfaced), OR
  - The user explicitly asks (`/next`, "what's next", etc.).
- Otherwise, just confirm the write in one line (e.g. `hiit → 1 (today) ✓ verify=1.0 + todoist closed`).

Track suppression mentally per-session: after you've shown a Next-up panel once, don't repeat it until the list changes or the user asks. The user knows what's there; they'll ask if they want it again.

## No-args Mode

When `/did` is called with **no arguments**:

1. **Read Toggl cache** at `~/.claude/skills/tg/cache.json`. If no timer running, output `No timer running.` and exit.
2. **Stop the Toggl timer** via `python3 ~/i446-monorepo/mcp/toggl_server/toggl_cli.py stop`. Update the tg cache (`running: null`). Parse the stop output to extract duration in minutes. Apply the d359 bump (Step C) to the stopped entry's tags, same logic as `/tg stop`, scan for `d359/<slug>` and update `last_contact` in the matching d359 file.
3. **Check for /do session.** Read `~/.claude/skills/do/active.json`. If it exists and is valid:
   - The task is a variable-point activity started by `/do`.
   - Use the duration (minutes) from step 2 as the points value.
   - Route as `/did <task_name> <duration_minutes>` where `task_name` comes from `active.json`.
   - After success, delete `active.json` to clear the session.
   - Skip to step 5 (do NOT use the raw timer description as input).
4. **Standard path (no /do session).** Use the timer description as the /did input. Strip Toggl-specific prefixes/noise, then route through the normal /did pipeline (Steps -2 to 6) as if the user had typed `/did <description>`.
5. **Surface next tasks.** After launching the /did background agent, read `~/.claude/skills/next/cache.json` and display the top 9 tasks (same format as `/next`). Ask user to pick one (1-9) or skip. If they pick, start a Toggl timer via the CLI and update the tg cache.

This lets the user finish a task and immediately start the next one in a single flow: `/did` → stop timer → mark done → pick next → start timer.

## Parsing (Steps -2 to 0.5)

**Date:** Last token `yesterday` or `M/D` → strip and set `targetDate`. Default: today (M/D format).

**Split:** `,` or `;` → separate items, process each independently.

**Aliases:** `stats m5x2`→`stats m5x2`, `math`→`问学`, `skin2skin`→`问学`, `wake up`→`起`

**Cumulative columns:** `问学` — add to existing value instead of overwriting.

**Cumulative 1n+ habits:** `一起饭` — routes to Step 1n but adds +30 per occurrence (not the row 3 value). Uses the cumulative variant of the 1n+ write template.

**@project override:** `@code` token → set `projectOverride`, strip from item.

**Time range:** `HHMM-HHMM` pattern → extract start/end, compute duration as `[time]`, set `hasTimeRange`.

## Routing (Step 0)

**Always run the dispatcher first.** Do not re-implement the matching logic in prose — it lives in code:

```bash
python3 ~/i446-monorepo/tools/did/route.py "<input>" [--target-date M/D]
```

The dispatcher emits JSON with the routing decision. Use its output directly:

| `step` field | Meaning | What to do |
|--------------|---------|------------|
| `"0n"`       | Registry-resolved 0₦ habit | Steps 1–4 using `neon_col`, `neon_sheet`, `domain`, `todoist_label`, `toggl` from JSON |
| `"1n+"`      | Registry-resolved 1n+ habit | Step 1n using `neon_col` (1n+ sheet), `domain`, `fen_col` for the 0分 append, `todoist_label` |
| `"unknown"`  | Not in registry — one-off | Fall through to **0.3** (Todoist word-overlap) and **6** (variable). The dispatcher's `hint` field will say so. |

The dispatcher has already:
- handled aliases (`math` → 问学, `stats m5x2` → m5x2-stats, `wake up` → 起)
- stripped `@project` overrides into `toggl.project`
- stripped `[N]`/`(N)`/`{N}` annotations from the routing query
- resolved live column letters via neon-cols.json (so writes go to the right place even after a column reshuffle)

If `step == "0n"` and target_date is past, treat as Step 6b (posthoc) instead of Steps 1–4.

### Fallback paths (only if `step == "unknown"`)

3. **Todoist match** (word overlap ≥0.6 across ALL pages, paginate with `next_cursor`) → Step 5. For one-off Todoist tasks (build-order goals, posthoc, ad-hoc adds).
4. **No match** → Step 6 (variable task).

Word overlap: tokenize both sides (lowercase, strip `[N]`/`(N)`/stopwords, **strip apostrophes and punctuation** so `mother's`→`mothers`, `don't`→`dont`), ratio = query words found in task / total query words. ≥0.6 matches. Tie: highest ratio. 0.4 only if exactly one task.

### Registering new recurring habits

If a one-off keeps recurring (you find yourself typing it weekly), register it as a real habit so the dispatcher hits next time:

```bash
~/i446-monorepo/scripts/register-habit.py <id> --name "..." --category {0n|1n+} \
    --neon-header <header> --domain <code> --create-excel-col --create-todoist
```

The `--create-excel-col` flag adds the column to the 0n/1n+ sheet via the daemon. `--create-todoist` creates the recurring Todoist task with the right labels. Registry, Excel, and Todoist stay in sync automatically.

## Step 1–4: 0₦ Habit Flow

**1b. Auto-detect time:** No `[time]` provided → check Toggl today for matching entries (description substring or project code match via /tg shortcode mapping). Sum minutes. No match → `1`.

**2. Write to 0₦:** Use "Write to 0₦" template from `applescript-ref.md`. Run via `osascript -e '...'`.

**2b. 0l special case:** If habit is `0l`, run "0l completion time" template.

**2c. Verify:** Check `verify=` in AppleScript return. Flag `⚠ checksum mismatch` if wrong.

**3. Close Todoist:** Search `0neon`-labeled tasks using **word overlap matching** (same algorithm as Step 0: tokenize both sides, ratio ≥ 0.6). Also search `夜neon`-labeled tasks (evening habits like `hcmc`). **Dash-normalization:** strip ` - ` from both sides before matching. **Alias expansion:** if the habit was resolved via alias (e.g. `math` → `问学`), search for BOTH the original input AND the alias. Close if found.

**3b. Validation gate:** BLOCKING — confirm Step 3 was attempted. If not → go back. This is the most common failure mode.

**4. Report:** `<habit> → <time> (today) [+ todoist] ✓ verify=<value>`

## Step 5: Todoist-only Task

Task found in Step 0. **Build order is the points source of truth, not the Todoist task content** — the user routinely edits annotations in the build order after `/0g`/`-1g` sync, and those edits must be respected.

**5a. Resolve points (do BEFORE writing to Excel) — priority order:**

1. **User-typed annotations on the /did command** win over everything. If the user wrote `[N]` or `{N}` in the /did input, use those values. They override stale build-order or Todoist values.
2. **Build order line.** If the task has label `关键径路` / `#0g` / `#-1g`, find the matching line in `~/vault/g245/-1₦ , 0₦ - Neon {Build Order}.md` (same matching algorithm as Step 5b below). If matched, extract `[N]` and `{N}` from that line for whichever annotation the user did NOT type explicitly.
3. **Todoist task content.** If no build-order match, fall back to `[N]`/`{N}` in the Todoist task content.
4. Empty/missing → 0 pts (per "Zero points default" memory).

The user-typed value is final — if they type `[30]` and the build order says `[100]`, write +30, not +100.

**5a-write. Write points to 0分:**

- `[N]` → domain column per label map: `i9`/`i447`/`f693`/`f694` → AA, `m5x2` → AB, `g245`/`infra`/`cc` → AC, `hcmc` → AD, `xk87`/`xk88` → AG, `s897` → AH, `hcb`/`hcbp` → AF, `hcm`/`hcmp` → AE
- `{N}` → **always AC** (the 0g column), regardless of task labels
- If both `[N]` and `{N}` are present, do TWO sequential writes (one per column).
- If both are 0 / missing, skip the Excel write entirely.

Append via "Append to 0分" template. Close the Todoist task.

**5b. Build order check-off:** If the closed task has label `关键径路` (or `#0g`/`#-1g`), flip the matching line from `- [ ]` to `- [x]`:

- Search `## 0₲` section (before `### 以后的目标`), 2-space indent: `  - [ ] <content>` → `  - [x] <content>`
- Then search `## -1₲` section within any 地支 block (寅/卯/辰/…), 4-space indent: `    - [ ] <content>` → `    - [x] <content>`

Match the line whose bullet content equals the Todoist task content (preserve `(N)`, `[N]`, `{N}` annotations — they're written identically by `/0g` and `/-1g`). If no exact match, fall back to substring match on the bare goal text (strip `(N)`/`[N]`/`{N}` from both sides for the comparison). If still no match, skip silently — don't fail the /did flow.

**5.5.** If `hasTimeRange`, create Toggl entry via `toggl_create_entry`.

## Step C (cross-cutting): d359 last_contact bump

**Runs after EVERY Todoist task close in /did** (Steps 1–4 / Step 5 / Step 1n / Step 6b). Cheap to scan, skip silently if no match.

For each label on the closed task matching `d359/<slug>`:
1. Find `vault/d359/<slug>*.md` (glob — handles `<slug>-d359.md` and `<slug> d359.md` variants).
2. If file exists, update or insert `last_contact: <targetDate>` in the frontmatter.
3. If no file matches the slug, log a one-line warning and continue (don't fail the /did flow).

Convention: `d359/<slug>` labels mark Todoist tasks (and Toggl tags) as outreach for that contact. Same convention used by `/done` and `/tg stop` for time entries. Slug = d359 filename minus `-d359.md` / ` d359.md` suffix (e.g., `mark-mckay`, `mariah-mckay`).

## Step 6: Variable Task

No 0₦ or Todoist match. Number = **points** not minutes.

1. Infer domain (or use `projectOverride`): social→s897(AH), family→xk87(AG), health→hcb(AF), work→m5x2(AB), tech→i9(AA), media→hcmc(AD), goals→g245(AC). Ambiguous → ask.
2. Append points to 0分.
3. Create posthoc Todoist task: `content + " @posthoc @YYYY-MM-DD"`, labels `["posthoc", "<domain>"]`, due `targetDate`. Immediately close it.

## Step 1n: 1neon Task

Matches 1n+ sheet header. Do NOT write to 0₦.

1. Find column + week row. **M.W** is computed from the **Sunday that starts the calendar week containing the target date** (weeks are Sun–Sat):
   - `sunday = target_date - timedelta(days=(target_date.weekday() + 1) % 7)` (Python; `weekday()` Mon=0..Sun=6)
   - `M = sunday.month`
   - `W = (sunday.day - 1) // 7 + 1`  ← which Sunday of `M` this is (1st, 2nd, 3rd, …)
   - Examples: Fri Apr 24 2026 → Sun Apr 19 → `4.3`. Sun Mar 29 2026 → `3.5`. Mon Apr 13 2026 → Sun Apr 12 → `4.2`.
   - Do NOT use `ceil(day/7)` — that gives the wrong week when the date's Sunday falls in a different month/week-of-month bucket. The 1n+ column B labels these Sunday-anchored weeks.

   Read points from row 3. Write points to cell. Use "1n+ write" template.
   - **Cumulative 1n+ habits** (e.g. `一起饭`): Instead of writing the row 3 value, **add the fixed increment** to the existing cell value (use the cumulative variant of the 1n+ write template — read old value, add increment, write sum). Fixed increments: `一起饭` = 30.
2. Append cell reference `+'1n+'!{col}{weekRow}` to 0分. Map column via `g245/1-neon-meta.md`. Use "1n+ → 0分" template. For 一起饭 → 0分 column AG (xk).
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
| `/did stats m5x2` — Todoist is "m5x2 stats (4) [8]" | Step 3 word-overlap matches despite word order | Must NOT skip Todoist close |
| `/did math` — alias→问学, Todoist is "math with kids (60) [70]" | Step 3 searches original "math" too, word-overlap matches | Must NOT skip Todoist close |
| `/did hiit` then `/did 0l` | completed-today filters hiit from suggestions | Must NOT re-suggest completed recurring tasks |
| `/did 1 xk88` — 1n+ header is "1 xk88" | Step 0.2 matches 1n+ header → Step 1n, closes 1neon Todoist task | Must NOT treat leading "1" as points and route to Step 6 |
| `/did 1 i9` — 1n+ header is "1 i9" | Step 0.2 matches 1n+ header → Step 1n | Must NOT route to Step 6 as "1 point to i9" |
| `/did PTC` — Todoist match "PTC feedback [180]" with label xk87 | Step 5: extract [180] from task, write +180 to 0分 AG | Must NOT use 0 pts just because user input had no [N] — always extract from the matched Todoist task |
| `/did Use /inbound to keep response times low.` — Todoist task content has no `{N}`, but build order line has `Use /inbound to keep response times low.{30}` (user added post-sync) | Step 5a: extract `{30}` from BUILD ORDER line, write +30 to 0分 AC (0g column), close Todoist, flip checkbox | Must NOT use 0 pts because Todoist task lacks `{30}` — build order is the points source of truth |
| `/did 1 hcb` on Fri 2026-04-24 — 1n+ Step 1n flow | Compute M.W from the Sunday starting that calendar week (Sun Apr 19 → `4.3`, row 19). Write to U19. | Must NOT use `ceil(day/7)` (gives 4.4, row 20 = NEXT WEEK). 1n+ column B labels are Sunday-anchored Sun–Sat weeks. |
