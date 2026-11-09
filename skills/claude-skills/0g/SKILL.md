---
name: "0g"
description: "Set or sync daily goals (0₲). With args: writes goals to build order + Todoist. Without args: syncs existing build order goals to Todoist. Usage: /0g [goals]"
user-invocable: true
---

# Daily Goals (/0g)

Manage daily goals in the `## 0₲` section of the build order file and sync to Todoist.

## dtd claim

The claim hook ties this session to the `0g` dtd card the moment `/0g` is
submitted (a `[claim hook] 😈 claimed: 0g ...` line in context), so dtd shows it
in progress while the skill runs. Don't claim it again. The skill closes the
card (`did-fast.py "0g"`) and then releases the claim in its last step. If the
card is already done today, nothing is claimed and the release is a no-op.

## Files

- **Build order**: `~/vault/g245/5e-1/build-order.md`
- **Section**: `## 0₲` (stop at the next `##` or `###` heading)
- **Todoist project**: the goal's **domain project** (i9 → `6XQ3GMQRVmPgPM4W`, m5x2 → `6Crfmq5Pjp462w3C`, xk87 → `6Crfmq5QFg895Mcw`, hcbp → `6Crfmq5Q33gJ5Q3P`, g245 → `6Crfmq5PpX4JhP4c`, …). A goal with no inferable domain stays in `0g` (ID: `6XfvCQ3p8Gq6fhGR`). JM 2026-11-09: "once I make goals, infer the project and add that as the 'project' for the tasks."
- **Todoist labels**: `#0g` on every task, plus the domain label when there is one
- **Routing is computed, not guessed here.** `scripts/0g-sync.py` (the 2-hourly cron that also files build-order goals) owns the keyword table and the domain → project map. Ask it, one JSON line per goal:
  ```bash
  python3 ~/i446-monorepo/scripts/0g-sync.py route "<goal 1>" "<goal 2>" ...
  # → {"content": ..., "domain": "hcbp", "labels": ["#0g", "hcbp"], "project_id": "6Crfmq5Q33gJ5Q3P"}
  ```
  Use its `labels` and `project_id` verbatim. An explicit `@code` in the goal text wins (the script matches it); strip the `@code` from the Todoist content afterwards.

## Notation Parsing

Goals may include these inline annotations:

| Syntax | Meaning | Maps to |
|--------|---------|---------|
| `(N)` | Expected time in minutes | Todoist `duration` |
| `[N]` | Points for the domain (`@project`) | Store in task text; use when logging completion |
| `@code` | Domain/project (e.g. `@i9`, `@m5x2`) | Todoist label (strip from content) |
| `{N}` | Bonus 0g points → 0分 Z column on completion | Store in task text as `{N}` |

Strip `@code` from the displayed task content in Todoist (it becomes a label). Keep `(N)`, `[N]`, and `{N}` in the task content as-is — they are read by `/did` and other skills at completion time.

## Domain Auto-Categorization

Done by `0g-sync.py route` (above) — do not hand-infer. Its rules: explicit codes (`i9`, `m5x2`, `@xk87`, …) first, then keywords per domain (Microsoft/Xbox/GitHub → `i9`; property/tenant/Ian/Stefanie → `m5x2`; Theo/Ren/school/family/home → `xk87`; HIIT/bball/gym → `hcbp`; food/meal/sleep → `hcb`; meditation/journal/dream → `hcm`; news/podcast/read → `hcmc`; invest/tax → `qz12`; friends/party → `s897`; admin/claude/dtd → `i447`; goal/review/plan/分 → `g245`). Edit `DOMAIN_RULES` in `scripts/0g-sync.py` to change routing; the cron and this skill then agree by construction.

**No match → no domain label and the task stays in the `0g` project.** Do not default to `g245`: the cron does not, and the two paths must file the same goal the same way.

## Behavior

### Step 0: Toggl 0g time carve-out

Before any goal processing, ensure Toggl has a 0g entry for the planning time:

1. Check current Toggl timer via `toggl_cli.py current`.
2. If a timer is running and its description already contains "0g", skip this step.
3. If a timer is running but is NOT 0g:
   a. Read the entry's ID, description, project, and start time.
   b. Update the running entry's `stop` to (now - 2 minutes) via `toggl_api.update_entry(entry_id, stop=<iso>)`. Use this Python one-liner through Bash:
      ```bash
      python3 -c "
      import sys; sys.path.insert(0, '$HOME/i446-monorepo/mcp/toggl_server')
      import toggl_api; from datetime import datetime, timezone, timedelta
      two_ago = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
      toggl_api.update_entry(ENTRY_ID, stop=two_ago)
      print('Trimmed to', two_ago)
      "
      ```
   c. Create a new 2-minute completed entry: `toggl_cli.py create "0g" <HH:MM-2min> <HH:MM> g245`
   d. Start a new timer with the SAME description and project as the one that was running: `toggl_cli.py start "<original desc>" <original project>`
4. If NO timer is running:
   a. Create a 2-minute completed entry: `toggl_cli.py create "0g" <HH:MM-2min> <HH:MM> g245`

This ensures planning time is always tracked, even retroactively.

### With arguments — set new goals

Goals are provided as arguments after `/0g`. May be bullet lines, comma-separated, or free text (one goal per sentence/line).

**Step 1: Parse goals**

Each line/item becomes one goal. Strip leading `-`, `*`, `[ ]`, `[x]` markers. Parse and extract:
- `(N)` → duration in minutes
- `[N]` → point value
- `@code` → domain label (remove from content)
- `{N}` → bonus points (keep in content)

**Point-bracket normalization (default to `{N}`).** A 0g goal's completion points belong in the **0g column**, which `/did` credits only from `{N}` (curly). `[N]` routes to the goal's *domain* column instead, so it silently misfiles the points (bug 2026-07-11: a `/0g` run wrote `[100]`/`[60]`/`[40]` and those goals would have credited hcbp/xk rather than 0g). Therefore:
- If a goal carries neither `{N}` nor `[N]`, **auto-estimate and append `{N}`** (per the bonus budget in `vault/g245/neon-habit-principles/fen.md` (Goaling bonus budget), 0g ≤ 100/day: quick/routine `{15}`, medium `{20}`–`{30}`, ambitious `{40}`, cap `{50}`). Never leave a 0g goal without a point bracket.
- If the user typed `[N]` with no `{N}`, treat it as `{N}` (convert to curly) unless they *explicitly* want the points on the domain column. Daily 0g goals should carry `{N}`.
- Never default un-annotated 0g goals to `[N]`.

**Step 2: Update build order**

Read the build order file. Find `## 0₲`. Replace the existing goal items (lines matching `  - [ ]` or `  - [x]`) with the new goals formatted as:
```
  - [ ] <goal content>
```
Preserve the `### 以后的目标` subsection and everything below it. Only replace the items between `## 0₲` and the next subsection/heading.

**Step 2.5: Durably log goals (set-time archive)**

Immediately record the goals to `~/vault/g245/0g-log.md` so they survive the daily reset, the `0₲ → 以后的目标` move, and vault-sync conflicts. The end-of-day reset only logs goals still sitting in `## 0₲` at 04:00, so goals set and later moved were silently lost (fixed 2026-06-27). Pass each goal's content verbatim (keep `(N)`/`[N]`/`{N}`; `@code` may be left in or stripped — the logger normalizes):

```bash
python3 ~/i446-monorepo/scripts/0g_log.py "<goal 1 content>" "<goal 2 content>" ...
```

The logger is idempotent and merges into today's section, so running `/0g` several times a day accumulates rather than duplicates.

**Step 3: Add to Todoist (with dedup)**

First, fetch existing open `#0g` tasks using `find-tasks` with `labels: ["#0g"]` (goals live in their domain projects now, so a `projectId` dedup would miss them). For each goal, skip creation if an open task with matching content already exists (compare after stripping whitespace; substring match is sufficient).

For each **new** goal (no existing match), run `0g-sync.py route` on it and create a Todoist task:
- **Content**: goal text (with `(N)`, `[N]`, `{N}` preserved; `@code` stripped)
- **Project**: the route's `project_id` (the domain's project; `0g` when no domain)
- **Labels**: the route's `labels` (`["#0g"]` or `["#0g", "<domain>"]`)
- **Priority**: `p1`
- **Due**: today
- **Duration**: from `(N)` if present

**Step 4: Mark 0g habit done**

Run `did-fast.py` directly (do NOT spawn an agent or invoke `/did`):
```bash
python3 ~/i446-monorepo/tools/did/did-fast.py "0g"
# Refresh the dtd task cache FOREGROUND so the new #0g goals show up in dtd. Run
# it foreground (NOT backgrounded with `&`): a backgrounded job gets torn down
# when the shell/tool call returns before the ~2s refresh finishes, so the goals
# never reach the cache and dtd never updates (regression 2026-06-30). The
# did-refresh-cache daemon also picks up #0g within ~3min as a fallback, but the
# foreground run makes /0g trigger the refresh immediately.
python3 ~/i446-monorepo/tools/did/did-fast.py --refresh-cache >/dev/null 2>&1
# The card is closed now: release the dtd claim the /0g prompt took, so its 😈
# spinner stops. Harmless no-op if nothing is claimed.
python3 ~/i446-monorepo/tools/did/agent_claims.py release >/dev/null 2>&1
```
This writes 1 to 0₦, closes the 0neon Todoist task, appends points to 0分, and stops any running 0g Toggl timer. The foreground `--refresh-cache` rebuilds `task-queue.json` (with the new #0g goals) so dtd's watcher reloads and surfaces them immediately.

**Step 5: Confirm**

```
0g → N goals set + synced to todoist
```

---

### Without arguments — sync existing goals to Todoist

**Step 1: Read build order**

Read the build order file. Extract all unchecked items (`- [ ]`) under `## 0₲` (before any `###` subsection).

If none found, report "No 0g goals in build order" and stop.

**Step 1.5: Durably log goals (set-time archive)**

Record the goals to `~/vault/g245/0g-log.md` so they survive the daily reset / `0₲ → 以后的目标` move / sync conflicts (see the with-arguments path for why):

```bash
python3 ~/i446-monorepo/scripts/0g_log.py "<goal 1 content>" "<goal 2 content>" ...
```

**Step 2: Add to Todoist (with dedup)**

First, fetch existing open `#0g` tasks using `find-tasks` with `labels: ["#0g"]` (dedup across every domain project, not just `0g`). For each unchecked goal, skip creation if an open task with matching content already exists (substring match). Create only non-duplicate tasks (same format as above — `0g-sync.py route` for project + labels, annotations parsed from the text).

**Step 3: Mark 0g habit done**

Run `did-fast.py` directly (do NOT spawn an agent or invoke `/did`):
```bash
python3 ~/i446-monorepo/tools/did/did-fast.py "0g"
# Refresh the dtd task cache FOREGROUND so the new #0g goals show up in dtd. Run
# it foreground (NOT backgrounded with `&`): a backgrounded job gets torn down
# when the shell/tool call returns before the ~2s refresh finishes, so the goals
# never reach the cache and dtd never updates (regression 2026-06-30). The
# did-refresh-cache daemon also picks up #0g within ~3min as a fallback, but the
# foreground run makes /0g trigger the refresh immediately.
python3 ~/i446-monorepo/tools/did/did-fast.py --refresh-cache >/dev/null 2>&1
# The card is closed now: release the dtd claim the /0g prompt took, so its 😈
# spinner stops. Harmless no-op if nothing is claimed.
python3 ~/i446-monorepo/tools/did/agent_claims.py release >/dev/null 2>&1
```
This writes 1 to 0₦, closes the 0neon Todoist task, appends points to 0分, and stops any running 0g Toggl timer. The foreground `--refresh-cache` rebuilds `task-queue.json` (with the new #0g goals) so dtd's watcher reloads and surfaces them immediately.

**Step 4: Confirm**

```
0g → N goals synced to todoist
```

## Response Style

Minimal. Confirm in one line. Do NOT explain. Do NOT ask for confirmation.
