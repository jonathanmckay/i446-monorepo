---
name: exp-weekly-dashboard
description: >
  Build the XBOX Experimentation weekly card-based dashboard showing what
  A/B experiment steps started, stopped, or are currently running last week.
  Pulls live data from the ExP MCP server, classifies each stopped step's
  outcome (Promoted / Rolled out / Completed / Stopped), and renders a
  self-contained HTML file with EXP and Scorecard links per card.
  Triggers: "weekly experiment dashboard", "weekly experiments", "exp weekly",
  "last week experiments", "what shipped this week", "build the weekly dashboard".
---

# ExP Weekly Experiment Dashboard

Build a self-contained HTML dashboard summarizing last week's Xbox A/B experiment
activity in four sections: **Experiments Ready for Review**, **Stopped**,
**Started**, and **Currently active**.

## What the user gets

A single HTML file (`weekly_dashboard.html`) grouped by Feature with:
- **Experiments Ready for Review** section (split into "Newly ready" and "Previously ready")
- Feature cards with collapsible accordion showing steps grouped by stage (GIA > Omega > Beta > Canary)
- Each step row shows: name, split, start date, end date, duration, decision, scorecard link
- Steps running ≥14 days get a sub-row indicator ("🎯 2-week scorecard available")
- Sticky nav bar with jump links and counts

## Prerequisites

- ExP MCP server configured (`exp.azure.net/mcp`) with `useMicrosoftCredential: true`
- Python 3.10+ available (no third-party packages needed)
- This skill folder is on disk somewhere the user can `cd` into

## Constants

| Key | Value |
|-----|-------|
| Workspace ID | `3e3b8347-c494-4f7e-a5aa-3e693ba7dd3c` (Xbox) |
| Auth | `useMicrosoftCredential: true` |
| Renderer | `build_weekly_cards.py` (in this skill folder) |
| Data file | `weekly_data.json` (written by you, read by the renderer) |
| Output | `weekly_dashboard.html` |

---

## Workflow

Execute steps 0–6 in order. **Do not skip steps.** Each step lists its inputs,
the exact MCP query, and what to record.

### Step 0 — Determine the week window

Compute "last week" as Monday 00:00 UTC through Sunday 23:59 UTC immediately
preceding today. Save the ISO strings as `week_start` and `week_end` for later.

Confirm the dates with the user once before fetching anything. If today is
Monday or early Tuesday, double-check whether they want the prior 7 days or
the prior calendar week.

---

### Step 1 — Pull the three step buckets

Run these three MCP queries in parallel.

**1a. Started last week**

```
mcp_exp-mcp_query_experiment_entity
  type: ExperimentStep
  filter: StartedAt ge {week_start}T00:00:00Z and StartedAt le {week_end}T23:59:59Z
  orderby: StartedAt desc
  top: 100
```

**1b. Stopped last week**

```
mcp_exp-mcp_query_experiment_entity
  type: ExperimentStep
  filter: StoppedAt ge {week_start}T00:00:00Z and StoppedAt le {week_end}T23:59:59Z
  orderby: StoppedAt desc
  top: 100
```

**1c. Currently active (AB only)**

```
mcp_exp-mcp_query_experiment_entity
  type: ExperimentStep
  filter: StartedAt lt {week_start}T00:00:00Z and StoppedAt eq null and AnalysisType eq 'AB'
  orderby: StartedAt desc
  top: 100
```

> **Known MCP quirk:** some versions of `exp-mcp` ignore the `filter` parameter
> on `ExperimentStep` queries and return the most recent 10 steps regardless.
> If you see fewer than 5 results or all results outside your window, fall back
> to: pull the experiment-level entities directly via Step 3, then expand each
> experiment's `Steps[]` array and filter dates in Python yourself.

For each step record, extract: `stepId` (id), `experimentId`, `stepName`
(displayName), `startedAt`, `stoppedAt`, `analysisType`, and `splits`
(stringify variant percentages, e.g., `"50/50"`, `"33/33/33"`, `"100%"`).

---

### Step 2 — Dedup the buckets

A step that both started AND stopped this week (common for fast progressive
rollouts) appears in both 1a and 1b. **Remove it from `started` so it only
shows under `stopped`.**

Result: three disjoint lists keyed by `stepId`.

---

### Step 3 — Enrich every unique experiment

Collect every distinct `experimentId` across all three buckets. Query each one:

```
mcp_exp-mcp_query_experiment_entity
  type: Experiment
  entityId: <experimentId>
```

Extract per experiment:
- `featureId`
- `displayName` (experiment name, used as card subtitle)
- `audienceFilterExpression` → flatten to a short human label (e.g., `"playxbox prod"`, `"Public Beta"`, `"Windows.Xbox GlobalAudience + many rings"`)
- The full `Steps[]` array — keep this in memory; you need it in Step 5

Cache results: do not re-query the same `experimentId` twice.

---

### Step 4 — Enrich every unique feature

Collect every distinct `featureId` from Step 3. Query each one:

```
mcp_exp-mcp_query_experiment_entity
  type: Feature
  entityId: <featureId>
```

Extract: `displayName`, `description`, `experimentationGroup` (e.g., `xbox~playxbox`).

Cache results.

---

### Step 5 — Compute `nextStepName` for stopped steps

For each step in the `stopped` bucket, look up its parent experiment's
`Steps[]` from Step 3, sort by `startedAt`, and find the step whose `startedAt`
is the smallest value greater than the current step's `stoppedAt`.

- If found: record its `displayName` AND an indication of what kind of step it is.
  Convention: append `(50/50 AB)` if it's an AB step, `(100% DC)` if it's a
  DataCollection step at 100%. Examples: `"Step 2 (50/50 AB)"`, `"Step-2 (100% DC)"`, `"Switch to 100% (DC)"`.
- If no next step exists: set `nextStepName = null`.

**Do not classify the outcome yourself.** The renderer derives Promoted /
Rolled out / Completed / Stopped deterministically from `nextStepName` +
`analysisType`. Your job is just to capture the next step name accurately.

---

### Step 6 — Write `weekly_data.json` and render

Assemble the final JSON in this shape (full schema in `references/weekly_data.schema.md`):

```json
{
  "week_label": "May 25 – May 31, 2026",
  "week_start": "2026-05-25",
  "week_end": "2026-05-31",
  "generated_for_date": "2026-06-01",
  "features":    { "<featureId>":    { "displayName": "...", "description": "...", "experimentationGroup": "..." } },
  "experiments": { "<experimentId>": { "featureId": "...", "displayName": "...", "audience": "..." } },
  "buckets": {
    "started": [ { "stepId", "experimentId", "stepName", "startedAt", "stoppedAt": null, "analysisType", "splits" }, ... ],
    "stopped": [ { ...same fields..., "stoppedAt": "...", "nextStepName": "..." }, ... ],
    "active":  [ { "stepId", "experimentId", "stepName", "startedAt", "analysisType", "splits" }, ... ]
  }
}
```

Write the file next to `build_weekly_cards.py`, then run the renderer:

```powershell
python build_weekly_cards.py
```

Or run the full live path (data pull + render in one step):

```powershell
python generate_live_weekly_data.py --render
```

The renderer:
- Groups steps by Feature → Stage (GIA > Omega > Beta > Canary)
- Filters to `analysisType == "AB"` (feature switches hidden, but counted)
- Computes outcome from `nextStepName` (see `_compute_outcome` in the script)
- Assigns features to sections: active > started > stopped (features with any active step go to Active)
- Builds "Experiments Ready for Review" section (steps running ≥14 days)
- Excludes validation groups and test experiments
- Writes `weekly_dashboard.html` to the same folder

Open the file in a browser to verify, then hand the path to the user.

---

## Dashboard Sections

| Section | Content | Ordering |
|---------|---------|----------|
| **Experiments Ready for Review** | Features with steps running ≥14 days. Split into "Newly ready" (14–21 days) and "Previously ready" (21+ days) | Newly ready first |
| **Stopped** | Features where all steps stopped this week | By stop date |
| **Started** | Features with newly started steps (no active steps from before) | By start date |
| **Active** | Features with steps still running | By start date |

## Outcome Classification (for stopped steps)

| Condition | Outcome | Badge |
|-----------|---------|-------|
| `nextStepName` contains `"AB"` or `"A/B"` | Promoted | green |
| `nextStepName` contains `"DC"`, `"DataCollection"`, or `"100%"` | Rolled out | accent |
| `nextStepName` contains `%` | Promoted | green |
| No next step + `analysisType == DataCollection` + 100% split | Completed | accent |
| No next step otherwise | Stopped | red |

---

## Anti-patterns

- **Don't pre-classify outcomes manually.** The renderer computes it from
  `nextStepName`. Just give it accurate `nextStepName` values.
- **Don't query the same experiment or feature twice.** Cache aggressively —
  20+ steps may share 5–10 experiments and 3–5 features.
- **Don't include DataCollection steps in the cards.** They're feature switches,
  not experiments. The renderer filters them out, but you should still pull
  them in Step 1 so the dedup in Step 2 works correctly.
- **Don't truncate the description in the JSON.** The renderer truncates to
  280 chars; store the full text so it can be re-rendered with different limits.
- **Don't assume the MCP filter works.** See the quirk note in Step 1.

## What to report back

When done, print:

| Item | Value |
|------|-------|
| Week window | `YYYY-MM-DD` → `YYYY-MM-DD` |
| Started (AB / total) | `N / M` |
| Stopped (AB / total) | `N / M` |
| Currently active | `N` |
| Ready for review | `N` (X newly ready, Y previously ready) |
| Output file | `weekly_dashboard.html` (absolute path) |
| Unresolved steps | List any step IDs that couldn't be mapped to an experiment |
