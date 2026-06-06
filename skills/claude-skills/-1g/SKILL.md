---
name: "-1g"
description: "Set goals for the current 2-hour block. Writes to build order and syncs to Todoist with #-1g. Usage: /-1g [next] <goals>"
user-invocable: true
---

# Set 2-Hour Block Goals (/-1g)

Set goals for the current (or next) 2-hour time block. Goals go into the `-1₲` section of the build order file and into Todoist with the `#-1g` label.

## Arguments

```
/-1g [next] <goals as bullet points or free text>
```

- If `next` appears as the first word, target the NEXT time block instead of the current one.
- Goals can be provided as bullet lines, comma-separated, or free text (one goal per line/item).

## Time Block Mapping

Auto-detect from wall clock time (America/Los_Angeles):

| Block | Local Time | 地支 |
|-------|-----------|------|
| 0 | 04:00-05:59 | 卯 |
| 1 | 06:00-07:59 | 辰 |
| 2 | 08:00-09:59 | 巳 |
| 3 | 10:00-11:59 | 午 |
| 4 | 12:00-13:59 | 未 |
| 5 | 14:00-15:59 | 申 |
| 6 | 16:00-17:59 | 酉 |
| 7 | 18:00-19:59 | 戌 |
| 8 | 20:00-21:59 | 亥 |

Times outside 04:00-21:59 default to 卯 (block 0).

## Files

- **Build order**: `~/vault/g245/-1₦ , 0₦ - Neon {Build Order}.md`
- **Section**: `## -1₲` — goals go under the matching 地支 time heading
- **Todoist project**: `0g` (ID: `6XfvCQ3p8Gq6fhGR`)
- **Todoist labels**: `#-1g` on every task, plus an auto-inferred domain label

## Domain Auto-Categorization

If a goal has an explicit `@code` annotation (e.g. `@i9`, `@m5x2`), use that as the domain label and strip it from the content. Otherwise, infer the domain from keywords in the goal text:

| Keywords | Domain |
|----------|--------|
| xbox, platform, copilot, github, teams, metrics, standup, sprint, retro, SLT, 1:1 (work context), PR, deploy, pipeline, experimentation, forza, halo, activation | `i9` |
| property, tenant, lease, rent, AppFolio, occupancy, maintenance, P&L, bookkeeping, eviction, unit, renewal, vacancy | `m5x2` |
| school, math, kids, Theo, Ren, Aurora, homework, reading, lego, PTC | `xk87` |
| exercise, gym, HIIT, bball, basketball, run, walk, yoga, stretch, legs | `hcbp` |
| eat, food, meal, calories, nutrition, breakfast, lunch, dinner, snack | `hcb` |
| meditat, journal, prayer, reflect, o314, 冥想 | `hcm` |
| news, 新闻, YouTube, podcast, read (media), article, book review | `hcmc` |
| invest, stocks, portfolio, taxes, budget, finance | `qz12` |
| goal, review, plan, weekly, 1s, 0g, dream | `g245` |
| social, friends, dinner out, call (non-work) | `s897` |
| admin, tooling, setup, infra, fix computer, claude, system | `i447` |

If no keywords match, default to the block's typical domain (from the block→domain mapping in -2n.py: 巳/午/未/申 → `i9`, 酉 → `m5x2`, 戌 → `xk87`, 亥 → `hcm`).

## Steps

### Step 1: Determine time block

Get current local time (America/Los_Angeles). Compute which block using `(hour - 4) // 2`, clamped to 0-8. If `next` argument is present, add 1 (clamped to 8).

### Step 2: Parse goals

Extract goal items from the user's input. Each line starting with `-` or `*` or a numbered list is one goal. If no list markers, split by newlines. Strip checkbox syntax if present (e.g., `- [ ] foo` becomes `foo`). Preserve `{N}` bonus point annotations.

**Auto-estimate bonus points:** If a goal does NOT already contain `{N}`, estimate and append one. Use `{5}` to `{20}` based on the goal's ambition and difficulty:
- Quick/routine tasks (send a message, check something, review): `{5}`
- Medium tasks (write something, prep, research): `{8}` to `{12}`
- Ambitious/hard tasks (deep work, build, create, ship): `{15}` to `{20}`

Always add the estimate. Never leave a goal without `{N}`.

### Step 3: Update build order markdown

Read the build order file. Find the `## -1₲` heading (match any line starting with `## -1₲`, ignoring trailing characters). If no match is found, error with "ERROR: ## -1₲ section not found in build order". Under the target 地支 heading (e.g., `- 辰`), replace any existing indented items with the new goals as `    - [ ] <goal>` lines (4-space indent).

Also add `🎯` to the block's header line (e.g., `- 巳` becomes `- 巳 🎯`) if it is not already present. Append it after any existing emojis/text on that line. **Only add 🎯 if at least one goal has non-empty text.** Do not mark a block as goals-set if all goals are blank.

Keep all other time blocks untouched.

### Step 4: Create Todoist tasks (with dedup)

First, fetch existing open tasks in the `0g` project (ID `6XfvCQ3p8Gq6fhGR`) using `find-tasks`. For each goal, skip creation if an open task with matching content already exists (substring match).

For each **new** goal (no existing match), create a Todoist task using the Todoist MCP `add-tasks` tool:
- **Content**: the goal text **including its `{N}` marker verbatim** (e.g. `tasks down to 70 {20}`, not `tasks down to 70`). Stripping the `{N}` causes /did to log 0 pts on completion.
- **Project**: `0g` (ID: `6XfvCQ3p8Gq6fhGR`) — use project name "0g"
- **Labels**: `["#-1g", "<domain>"]` (e.g. `["#-1g", "i9"]`)
- **Priority**: `p1`
- **Due**: `today`
- **Duration**: from `(N)` annotation if present (e.g., `(30)` → `"30m"`). If no `(N)`, omit.

### Step 5: Confirm

Output one line:
```
-1g → <地支 block name> (<HH:MM>-<HH:MM>): N goals set
```

## Response Style

Minimal. Execute and confirm in one line. Do NOT ask for confirmation. Do NOT explain what you're doing.
