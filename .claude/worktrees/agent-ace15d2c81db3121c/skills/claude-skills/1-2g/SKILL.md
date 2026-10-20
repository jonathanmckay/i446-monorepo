---
name: "1-2g"
description: "Audit Todoist tasks: add missing time estimates (N), point values [N], and domain labels. Skips #-1g, #0g, and -1neon tasks. Usage: /1-2g"
user-invocable: true
---

# Todoist Task Hygiene (/1-2g)

Scan all Todoist tasks (excluding `#-1g`, `#0g`, and `-1neon` labeled) and fix two things:
1. Missing time estimate `(N)` or point value `[N]` in the task content
2. Missing domain label

## Domain Label Mapping

Infer the correct domain label from keywords in the task content:

| Keywords / Patterns | Label |
|---------------------|-------|
| Microsoft, GitHub, PR, deploy, ship, SLT, sprint, standup, 1:1 (work context), code review, engineering, copilot, team, org | `i9` |
| property, tenant, rent, lease, AppFolio, m5x2, McKay Capital, Janowski, eviction, vacancy, renovation, unit, portfolio, real estate, lx, Louisa | `m5x2` |
| invest, stock, tax, portfolio (finance), budget, net worth, 401k, brokerage | `qz12` |
| Theo, Ren, Aurora, Rori, kids, family, xk87, school, park, playground, bedtime | `xk87` |
| Louisa, lx, wife, date night, anniversary | `xk88` |
| friend, dinner out, party, social, networking, coffee with, call with (non-work) | `s897` |
| exercise, gym, run, yoga, walk, stretch, hiit, bball, nutrition, meal prep, doctor, dentist, health | `hcb` |
| read, book, article, podcast, YouTube, news, language, 词汇, study, learn, course | `hcmc` |
| meditation, journal, o314, 冥想, reflect, therapy, mindfulness | `hcm` |
| goal, review, plan, neon, ritual, weekly review, quarterly | `g245` |
| infrastructure, admin, setup, tooling, move, housing, travel, flight, pack | `i447` |
| direct report, hiring, interview, perf review, calibration, f693 | `f693` |
| career, resume, job search, progression | `h335` |
| non-profit, charity, volunteer | `m828` |

If the task already has a label that matches a domain (i9, m5x2, g245, hcb, hcmc, xk87, xk88, s897, qz12, i447, i444, f693, h335, m828, hcm, hci, epcn, infra, hcbp, n156, 家), skip it — it already has a domain label. (`i444` = travel, added 2026-09-15 — it's a legitimate, consistently-used domain that was missing from this list, not a mislabel.)

If the task is in a Todoist project that maps to a domain, use that as a strong signal:

| Project ID | Domain |
|-----------|--------|
| 6XQ3GMQRVmPgPM4W | i9 |
| 6Crfmq5Pjp462w3C | m5x2 |
| 6Crfmq5PpX4JhP4c | g245 |
| 6Crfmq5Pw4Vc6rqF | qz12 |
| 6Crfmq5PmCxPmf2V | hcm |
| 6Crfmq5PmcCPc7PC | hcb |
| 6Crfmq5PmPjqrx4x | hci |
| 6PWgGPhJmxFp93Hf | hcmc |
| 6Crfmq5PpmP4jgfv | xk88 |
| 6Crfmq5QFg895Mcw | xk87 |
| 6Crfmq5Pp27hV9qM | s897 |
| 6Crfmq5PxX3vQ58m | f693 |
| 6H2WF96ChxjvMRcr | epcn |

## Estimation Heuristics

### Time `(N)` in minutes
| Task type | Estimate |
|-----------|----------|
| Quick action (send, check, review short doc, call) | 5–15 |
| Medium task (write, prep, fill out, research) | 20–40 |
| Large task (deep work, build, create, ship) | 60–120 |

### Value `[N]` in 分
| Impact level | Estimate |
|-------------|----------|
| Low (routine, admin, chores) | 3–8 |
| Medium (advance a project, connect) | 10–25 |
| High (strategic, unblocking, critical path) | 30–60 |

Value should generally be ≥ time estimate. If time > value, it's a signal the task may not be worth doing.

## Steps

### Step 1: Fetch all tasks

Use Todoist MCP `find-tasks` with `filter: "p1 | p2 | p3 | p4"` (matches every task regardless of due date — `find-tasks` requires at least one filter param, and this is a reliable catch-all since every task has some priority). Paginate with `next_cursor`/`cursor` to get ALL tasks.

**Exclude client-side, not via filter string.** Once fetched, drop any task whose `labels` array contains `#-1g`, `#0g`, or `-1neon` (exact string match against the array — do not re-fetch with a `!@#0g`-style filter clause). Bug found 2026-09-15: Todoist's `@label` filter syntax treats a leading `#` as its project-reference sigil, so a filter like `!@0g & !@-1g & !@-1neon` silently searches for labels literally named `0g`/`-1g` (no hash) and does NOT exclude the real `#0g`/`#-1g`-labeled tasks — confirmed live when 3 `#0g`/`#-1g` tasks leaked through that exact filter. Client-side array matching has no such ambiguity.

(`-1neon` block-ritual cards — `سمش`/`-1g`/`-1ibx` — score 0分!P from the block-header emoji, never from `[N]`, so stamping a `(N) [N]` estimate on them is wrong and shows bogus points in `/inbound`.)

### Step 2: Audit each task

For each task, check:
1. **Has `(N)`?** — regex match for `\(\d+\)` in content
2. **Has `[N]`?** — regex match for `\[\d+\]` in content
3. **Has a domain label?** — check if any label matches a known domain code

**Variable tasks exception:** If a task has empty parens `() []`, it is a variable-duration task and is acceptable as-is. Do not flag these for missing time or value estimates.

### Step 3: Build change list

For each task needing changes, prepare:
- New content with `(N)` and `[N]` appended if missing
- New labels array with domain label added if missing

Present a summary table to the user:

```
## Tasks to update (N total)

| # | Task | Changes |
|---|------|---------|
| 1 | Buy groceries | + (15) [5] @xk87 |
| 2 | Review PR #456 | + (20) [15] |
| 3 | Call dentist | + @hcb |
...
```

### Step 4: Confirm

Ask: **Apply changes? (y/n)**

Only proceed if confirmed.

### Step 5: Apply changes

Use Todoist MCP `update-tasks` to update each task's content and labels. Batch where possible.

### Step 6: Report

```
1-2g → updated N tasks (M time estimates, K point values, J labels added)
```

### Step 7: Mark done

Execute `/did 1 -2g` — this is a 1n+ task. Follow the full `/did` flow (Step 1n) to write points to the 1n+ sheet and append to 0分.

## Response Style

Show the summary table, ask for confirmation, then apply. Keep output clean and scannable.
