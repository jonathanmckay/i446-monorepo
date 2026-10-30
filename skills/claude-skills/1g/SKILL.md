---
name: "1g"
description: "Weekly goals. No args: review last week (Sun-Sat): score each goal in the 2g+1g narrative doc against Neon points, 0n media cells and completed Todoist tasks, ask JM about the ones data can't settle, then write a one-emoji verdict sub-bullet under each goal. With args: set weekly goals for a domain in the 1g tab of Neon. Usage: /1g [YYYY-MM-DD] | /1g <domain>: <goals>"
user-invocable: true
---

# Review Last Week's Goals (/1g with no args, or /1g YYYY-MM-DD)

Scores each goal in last week's block of the monthly narrative doc
(`~/vault/g245/5e2/YYYY.MM 2g+1g-narrative.md`, the month the Sunday falls in,
heading like `- **Oct 4 – Oct 10** —`) and writes the verdict as a sub-bullet
under each goal. The week is **Sunday–Saturday**: no arg = the most recently
completed week; a date = the week containing it.

If the argument contains a `:` or is a domain code, it's the goal-setting mode
below, not a review.

### Step 1: Evaluate

```bash
python3 ~/i446-monorepo/tools/1g/1g_review.py [YYYY-MM-DD] > /tmp/1g-review.json
```

Per goal it returns `status`, `note`, `kind`, and `evidence` (the week's
completed Todoist tasks for that domain, plus keyword hits for free-text goals):

| Goal shape | How it's scored |
|---|---|
| `<N>分` | That domain's daily points (dashboard points cache = Neon 0分 per-day totals) summed Sun–Sat. ✅ ≥100%, 🟡 ≥80%, ❌ below |
| `<N> days good media` | Days whose 0n `1+` (AU) cell is blank or ≤ 0, i.e. no junk `hcmc #1` media; `per_day` shows the bad days |
| anything else | `❓` + evidence |

`needs_question: true` marks goals the data can't fully settle: free-text goals,
and points goals with extra text (`unscored_remainder`, e.g. "weight should be 192.x").

### Step 2: Judge, then ask before writing

For each `❓` / `needs_question` goal, read its `evidence`. If the evidence
clearly settles it, decide the emoji yourself and say why. Otherwise
**surface it to JM with AskUserQuestion before writing anything** (one question
per goal, up to 4 per call; options = ✅ done / 🟡 partly / ❌ not done, plus
what you found). Never guess a ✅.

Status emoji: ✅ met · 🟡 partly / close · ❌ missed · ❓ still unknown (only if
JM can't say either).

### Step 3: Write verdicts

Write `/tmp/1g-verdicts.json` (`doc` from Step 1 and a `verdicts` list, one entry per goal with its
`text` and `line_no` copied verbatim, the final `status` emoji and a short
`note` with the numbers, e.g. "729分 of 600分 (122%)") and apply:

```bash
python3 ~/i446-monorepo/tools/1g/1g_review.py --apply /tmp/1g-verdicts.json
```

Each verdict lands as `- <emoji> <note> <!--1g-->` one level under its goal;
re-running replaces it, never duplicates. Then open the doc in Obsidian
(`/usr/bin/open "obsidian://open?path=<urlencoded>"`) and report a one-line
summary per goal.

---

# Set Weekly Goals (/1g)

Set weekly goals for a domain in the `1g` tab of `Neon分v12.2.xlsx`. Estimates 分 (points) and focus bonus for each goal, then writes them to the correct section.

## Usage

```
/1g <domain>: <goals>     # explicit goals
/1g <domain>              # pull goals from domain source (see below)
```

- `<domain>` — domain code (i9, m5x2, hcmp, hcb, g245, hci, xk87, hcmc, s897)
- `<goals>` — numbered lines, bullet points, or newline-separated goals

Examples:
- `/1g m5x2: 1. Close out Janowski 2. Get answers to March questions 3. AI usage handed off to Matt`
- `/1g i9: SLT prep is solid, Recruiting daily, Ship the dashboard`
- `/1g m5x2` — reads goals from the m5x2 source tab

## Domain-only mode (no goals after `:`)

When called with just a domain and no explicit goals, read goals from the domain's source location:

| Domain | Source |
|--------|--------|
| m5x2 | `Neon分v12.2.xlsx` → sheet `m5x2` → col D, last non-empty row (one goal per numbered line) |

Read the source, parse the goals, estimate 分 and focus bonus, then **show a draft table in the terminal and wait for user corrections** before writing to the 1g tab. The user may:
- Adjust 分 values (e.g. `1 - 100分`)
- Use `()` to mean "0分 for this goal" (the points are already distributed across constituent tasks; the weekly goal itself scores 0 but retains its focus bonus)
- Approve with "yes" / "write" / "looks good"

## 1g Tab Structure

- Sheet name: `1g` in workbook `Neon分v12.2.xlsx`
- Col A: section headers (domain codes)
- Col D: Goal text
- Col E: 分 (task completion value)
- Col F: Focus Bonus (incentive for completing this week)
- Col G: % Done

### Section Header Rows

| Domain | Header Row | Goal Rows | Col A Value |
|--------|-----------|-----------|-------------|
| i9 | 4 | 5-11 | i9 |
| m5x2 | 12 | 13-19 | m5c7 |
| hcmp | 20 | 21-23 | hcmp |
| hcb | 24 | 25-27 | hcb |
| g245 | 28 | 29-31 | g245 |
| hci | 32 | 33-35 | hci |
| xk87 | 36 | 37 | xk87 |
| hcmc | 38 | 39 | hcmc |
| s897 | 40 | 41-44 | s897 |

**Important:** These row numbers drift (2026-10-30: i9 4, m5c7 13, hcmp 17, hcb 20, g245 26, hci 30, xk87 34, hcmc 38, s897 41, totals row 46). `1g_set.py` scans col A itself; the table is only a hint.

## Steps

### Step 1: Parse input

Extract `<domain>` (everything before the first `:`) and `<goals>` (everything after).

Parse goals by splitting on:
- Numbered lines (`1.`, `2.`, etc.)
- Bullet points (`-`, `*`)
- Newlines

Strip leading numbers, bullets, whitespace. Each item becomes one goal.

### Step 2: Find section in 1g

`1g_set.py` (Step 4) finds the section itself: the row whose col A, stripped
of surrounding whitespace, equals the domain (`m5x2` is spelled `m5c7`; A34
reads `" xk87"` with a leading space). Goal rows run from header+1 to the next
non-blank col A or the first row holding a formula in D..G (the totals row
under the last section, s897), whichever comes first. For domain-only mode,
`--dry-run` shows the section's rows without writing.

### Step 3: Estimate 分 and focus bonus

For each goal, estimate points using this heuristic:

| Goal type | 分 | Focus Bonus |
|-----------|---|-------------|
| Small discrete task (send email, make call, check in) | 10-20 | 20-40 |
| Medium task (complete deliverable, review, prep) | 30-60 | 50-100 |
| Large strategic goal (close deal, launch, ship) | 80-150 | 100-200 |
| Ongoing/monitoring task (track, monitor, follow up) | 15-30 | 30-60 |

Focus bonus is typically 1.5-2x the 分 value. Harder or more impactful goals get higher multipliers.

Use judgment based on the goal text. When in doubt, lean toward the middle of the range.

### Step 4: Write to 1g sheet

Write the goals as JSON with the Write tool, in order, to `/tmp/1g-goals.json`:

```json
[{"text": "Close out Janowski", "fen": 80, "focus": 150},
 {"text": "AI usage handed off to Matt", "fen": 30, "focus": 60}]
```

then:

```bash
python3 ~/i446-monorepo/tools/1g/1g_set.py <domain> /tmp/1g-goals.json
```

Each goal goes to row header+1+i: D = text, E = 分, F = focus bonus. The
section's leftover rows are blanked (D..G), but a cell holding a formula is
never touched. Every cell goes through the excel-http daemon (journaled in
the neon ledger, `src` = `1g <domain>`; Neon-off-Excel step 2c, 2026-10-30);
the section scan is a read-only ix-osa bulk read. More goals than the section
has rows is an error (exit 1, nothing written) rather than spilling into the
next section's header: tell JM and ask which to drop or whether to add a row
in Excel. Surface any non-zero exit verbatim; never fall back to local
`osascript` or a hand-written AppleScript.

### Step 5: Report

Output a table:

```
1g → <domain> (rows <start>-<end>):
  1. <goal> — <分>分 {<bonus>}
  2. <goal> — <分>分 {<bonus>}
  ...
```

## Response Style

- **With explicit goals:** Execute and confirm with the table. Do NOT ask for confirmation before writing.
- **Domain-only mode:** Show draft table in terminal and WAIT for user approval/corrections before writing.

Do NOT explain the estimation process — just show the results.
