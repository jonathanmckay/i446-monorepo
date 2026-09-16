---
name: "2n"
description: "Monthly Toggl coverage audit. Finds days in the most recent finished month with under 23:45 of tracked time, numbers every gap of 5+ minutes with a suggested fill (calendar meeting, 睡觉, or the neighbouring entry), and batch-creates the ones you pick. Usage: /2n [YYYY-MM] or /2n fill 1,3,5=睡觉,7=family time @xk87"
user-invocable: true
---

# Monthly Toggl Coverage (/2n)

Audit the most recent finished month for days whose Toggl entries don't add
up to at least 23:45, show where the holes are, and say what each hole most
likely was. Read-only by default; the user fills gaps afterwards (or asks for
`--fill`, see below).

## Usage

```
/2n                              # most recent finished month → numbered gap list
/2n 2026-07                      # a specific month
/2n --floor 23:30                # different coverage floor
/2n fill 1,3,5                   # create the suggested entries for gaps #1, #3, #5
/2n fill 2=family time @xk87     # override one gap's description (and project)
/2n fill 4=@睡觉                  # keep the suggested text, force the project
```

Days that are under the floor only because of seams shorter than 5 minutes
are fine (JM, 2026-09-16): they are named in one line and never listed or
numbered.

## Step 1: Run the audit

```bash
python3 ~/i446-monorepo/tools/2n/2n-coverage.py [YYYY-MM] [--floor-min N] --write
```

`--write` also saves the report to `~/vault/g245/reviews/YYYY-MM-2n.md`.
Convert a `--floor HH:MM` argument to minutes for `--floor-min` (23:45 →
1425, the default). Takes ~30–40s for a full month (Toggl is fetched in
7-day chunks to stay under the endpoint's per-request cap; Google Calendar
is read only for the days that have gaps, through `tools/tg/gcal_client.py`'s
per-day cache, so reruns are fast).

What the tool does:
- **Coverage** per local day = merged Toggl intervals clipped to the day.
  The last minute (23:59–00:00) counts as covered, because the day-barrier
  rule ends every overnight entry at 23:59 by design.
- **Floor** is 23:45 (`DEFAULT_FLOOR_MIN`). Days under it are listed.
- **Gaps ≥ 5 min** are listed, **numbered sequentially across the month**
  (#1, #2 …), each with a suggestion (`--min-gap` changes the threshold;
  coverage totals always count every seam). The numbered list is saved to
  `~/.local/state/jm/2n-last.json` so `fill` can act on it later.
- **Suggestion order**: (1) the calendar event overlapping most of the gap,
  with its `@code` from the calendar/keyword map (MSFT/Outlook → i9, m5x2
  Cal → m5x2, CAIS → xk87 …); (2) `睡觉` for a gap inside 21:00–07:00 that
  touches a 睡觉 entry or the day edge; (3) the same project on both sides →
  continue that entry; (4) a ≤10 min gap → extend the neighbour (low
  confidence); (5) `?`. A hole of 4h or more is never guessed as one thing:
  it lists the events the calendar shows inside it.
- Confidence is `high` / `med` / `low` per gap; only `high` is safe to
  fill without looking.

## Step 2: Report

Show the summary line and the per-day table verbatim, then the gap detail.
Do not paraphrase the suggestions away; the times and `@code`s are what the
user acts on. Mention the vault path.

If the tool errors with "no Toggl entries returned", the month is older
than Toggl v9's ~90-day reach; say so and stop.

## `/2n fill <spec>`: batch-create the picked gaps

When the first argument is `fill`, do NOT rerun the audit. Pass the rest
verbatim:

```bash
python3 ~/i446-monorepo/tools/2n/2n-coverage.py --fill "<spec>"
```

Spec grammar: comma-separated items, each `N` (use the saved suggestion),
`N=<description> @<code>` (override both), `N=<description>` (override the
text, keep the suggested project) or `N=@<code>` (keep the text, force the
project). Gaps whose suggestion is `?` or `multiple:` are skipped unless
overridden; the tool prints one `created`/`skipped` line per number and
exits 2 if anything was skipped. Echo those lines to the user. Entries are
created directly through `toggl_api.create_entry` with the gap's exact
start/end (gaps are already clipped inside one local day, so the
day-barrier rule holds).

If the user names gaps in prose ("fill 1 and 3, 2 should be lunch @hcb"),
translate to the spec yourself; do not ask them to retype it.

## Notes

- Tool: `~/i446-monorepo/tools/2n/2n-coverage.py`; tests alongside it
  (`test_2n_coverage.py`). `--json` dumps the audit structure.
- Toggl key comes from `TOGGL_API_KEY` or, failing that, the
  `toggl_server` MCP env block in `~/.claude.json`.
- The calendar→project map is a copy of `tools/janus/mobile.py`'s; keep
  them in step when adding calendars.
- Report naming follows the reviews folder: `YYYY-MM-2n.md` (calendar
  month, since the audit is calendar-month scoped, unlike the M.W week
  labels used by `/1s`).
