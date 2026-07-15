---
name: "0g"
description: "Set or sync daily goals (0₲). With args: writes goals to build order + Todoist. Without args: syncs existing build order goals to Todoist. Usage: /0g [goals]"
user-invocable: true
---

# Daily Goals (/0g)

Manage daily goals in the `## 0₲` section of the build order file and sync to Todoist.

## Files

- **Build order**: `~/vault/g245/5e-1/build-order.md`
- **Section**: `## 0₲` (stop at the next `##` or `###` heading)
- **Todoist project**: `0g` (ID: `6XfvCQ3p8Gq6fhGR`)
- **Todoist labels**: `#0g` on every task (plus the `@code` domain label if present)

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

If a goal has an explicit `@code` annotation, use that. Otherwise, infer the domain from keywords in the goal text:

| Keywords | Domain |
|----------|--------|
| xbox, platform, copilot, github, teams, metrics, standup, sprint, retro, SLT, 1:1 (work context), PR, deploy, pipeline, experimentation, forza, halo, activation | `i9` |
| property, tenant, lease, rent, AppFolio, occupancy, maintenance, P&L, bookkeeping, eviction, unit, renewal, vacancy | `m5x2` |
| school, math, kids, Theo, Ren, Aurora, homework, reading, lego, PTC | `xk87` |
| exercise, gym, HIIT, bball, basketball, run, walk, yoga, stretch, legs | `hcbp` |
| eat, food, meal, calories, nutrition, breakfast, lunch, dinner, snack | `hcb` |
| meditat, journal, prayer, reflect, o314, 冥想, dream | `hcm` |
| news, 新闻, YouTube, podcast, read (media), article, book review | `hcmc` |
| invest, stocks, portfolio, taxes, budget, finance | `qz12` |
| goal, review, plan, weekly, 1s | `g245` |
| social, friends, dinner out, call (non-work) | `s897` |
| admin, tooling, setup, infra, fix computer, claude, system | `i447` |

If no keywords match and no `@code` is provided, default to `g245` (daily goals are meta by nature).

The inferred domain is added as a Todoist label alongside `#0g`.

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
- If a goal carries neither `{N}` nor `[N]`, **auto-estimate and append `{N}`** (same scale as `/-1g`: quick/routine `{5}`, medium `{8}`–`{12}`, ambitious `{15}`–`{20}`). Never leave a 0g goal without a point bracket.
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

First, fetch existing open tasks in the `0g` project (ID `6XfvCQ3p8Gq6fhGR`) using `find-tasks`. For each goal, skip creation if an open task with matching content already exists in the project (compare after stripping whitespace; substring match is sufficient).

For each **new** goal (no existing match), create a Todoist task:
- **Content**: goal text (with `(N)`, `[N]`, `{N}` preserved; `@code` stripped)
- **Project**: `0g` (ID `6XfvCQ3p8Gq6fhGR`)
- **Labels**: `["#0g", "<domain>"]` — domain from explicit `@code` or auto-inferred (see Domain Auto-Categorization above)
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

First, fetch existing open tasks in the `0g` project (ID `6XfvCQ3p8Gq6fhGR`) using `find-tasks`. For each unchecked goal, skip creation if an open task with matching content already exists (substring match). Create only non-duplicate tasks (same format as above — parse annotations from the text).

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
```
This writes 1 to 0₦, closes the 0neon Todoist task, appends points to 0分, and stops any running 0g Toggl timer. The foreground `--refresh-cache` rebuilds `task-queue.json` (with the new #0g goals) so dtd's watcher reloads and surfaces them immediately.

**Step 4: Confirm**

```
0g → N goals synced to todoist
```

## Response Style

Minimal. Confirm in one line. Do NOT explain. Do NOT ask for confirmation.
