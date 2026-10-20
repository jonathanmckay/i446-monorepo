# `weekly_data.json` schema

The renderer (`build_weekly_cards.py`) consumes a single JSON file. This page
documents every field. See `examples/weekly_data.example.json` for a working
file.

## Top-level

| Field | Type | Notes |
|-------|------|-------|
| `week_label` | string | Human label shown in dashboard header. Example: `"May 25 – May 31, 2026"` |
| `week_start` | string (ISO date) | Inclusive Monday of the reporting week, e.g. `"2026-05-25"` |
| `week_end` | string (ISO date) | Inclusive Sunday of the reporting week, e.g. `"2026-05-31"` |
| `generated_for_date` | string (ISO date) | The day the dashboard treats as "today" (used to compute Day N for active steps) |
| `features` | object | Map of `featureId` → feature record |
| `experiments` | object | Map of `experimentId` → experiment record |
| `buckets` | object | Three lists: `started`, `stopped`, `active` |

## `features[<featureId>]`

| Field | Type | Source |
|-------|------|--------|
| `displayName` | string | `Feature.displayName` |
| `description` | string | `Feature.description` (full text; renderer truncates to 280 chars for display) |
| `experimentationGroup` | string | `Feature.experimentationGroup` (e.g., `"xbox~playxbox"`) |

## `experiments[<experimentId>]`

| Field | Type | Source |
|-------|------|--------|
| `featureId` | string (GUID) | `Experiment.featureId` |
| `displayName` | string | `Experiment.displayName` (used as card subtitle) |
| `audience` | string | Short human label derived from `Experiment.audienceFilterExpression` |

## `buckets.started[]` / `buckets.active[]` (step records, in-progress)

| Field | Type | Notes |
|-------|------|-------|
| `stepId` | string (GUID) | `ExperimentStep.id` |
| `experimentId` | string (GUID) | `ExperimentStep.experimentId` |
| `stepName` | string | `ExperimentStep.displayName` |
| `startedAt` | string (ISO datetime, UTC) | Required |
| `stoppedAt` | string \| null | Always `null` for `started` and `active` |
| `analysisType` | string | `"AB"` or `"DataCollection"`. Renderer hides DC steps from cards but keeps the count visible in section header |
| `splits` | string | Variant percentages, e.g. `"50/50"`, `"33/33/33"`, `"100%"` |

## `buckets.stopped[]` (step records, completed)

Same fields as above, plus:

| Field | Type | Notes |
|-------|------|-------|
| `stoppedAt` | string (ISO datetime, UTC) | Required for stopped steps |
| `nextStepName` | string \| null | Name of the next step in the same experiment that started after this one stopped. Used by the renderer to compute outcome. Conventions: `"Step 2 (50/50 AB)"`, `"Step-2 (100% DC)"`, `"Switch to 100% (DC)"`. `null` means no next step exists |

## Computed (not stored) — outcome classification

The renderer computes the outcome string + badge color from `nextStepName` +
`analysisType` + `splits` using these rules:

| Condition | Outcome | Badge |
|-----------|---------|-------|
| `nextStepName` contains `"AB"` or `"A/B"` | `Promoted` | green |
| `nextStepName` contains `"DC"`, `"DataCollection"`, or `"100%"` | `Rolled out` | accent-red |
| `nextStepName` matches `"Switch to N%"` or contains `%` | `Promoted` | green |
| `nextStepName` is null + `analysisType == DataCollection` + 100% split | `Completed` | accent-red |
| `nextStepName` is null otherwise | `Stopped` | red |
| Anything else | `Stopped` | amber |

If you find yourself wanting to override the outcome by hand, instead fix the
`nextStepName` value or extend `_compute_outcome` in the renderer.

## Computed — "Experiments Ready for Review" section

The renderer builds a top-level section showing features with active steps
that have been running long enough for a 2-week scorecard to be available.

**Classification logic:**

| Category | Condition | Meaning |
|----------|-----------|---------|
| **Newly ready** | Step running 14–21 days (crossed threshold this week) | First time this experiment is reviewable |
| **Previously ready** | Step running 21+ days | Was already past threshold last week |

Features appear in the review section AND in their normal Active/Started
section (they are duplicated, not moved). A feature is placed in "Newly ready"
if it has at least one step at 14–21 days; otherwise in "Previously ready" if
all review-eligible steps are 21+ days.
