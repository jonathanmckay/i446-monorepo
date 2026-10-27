---
name: "2hci"
description: "Monthly hci (image) review, the '2 hci' card due on the 26th. Over the 26th of last month through the 25th of this month: days 1st hci and 2nd hci earned full points (0n), plus points from other hci-labelled Todoist tasks. Then marks the card done. Usage: /2hci [month] [--no-mark]"
user-invocable: true
---

# Monthly hci review (/2hci)

Run the helper with the args verbatim and echo its output:

```bash
python3 ~/i446-monorepo/tools/2hci/2hci.py [month] [--no-mark] [--json]
```

## Window

26th of the previous month → 25th of the target month, the same offset the
daily cards use relative to the 1st. The card recurs on the 26th, so a run on
the card's day measures the window that ended yesterday. No arg = the most
recent completed window (on/after the 26th: ends this month's 25th; before:
last month's). `[month]` is the month the window ENDS in: `10`, `oct`,
`2026-10`.

## Metrics

- **1st hci / 2nd hci:** Neon `0n` columns found by row-1 header (`1st hci`,
  `2nd hci`; K/L as of 2026-10). 0分's BF formula credits the row-2 weight
  (26 / 7) for any non-blank cell, so **full = non-blank**. Explicit `0` is
  shown as n/a; values > 1 are session minutes and are summed as "timed".
  Read live from Excel on Ix via `ix-osa.sh`.
- **other hci:** Todoist completions in the window labelled `hci`
  (`/api/v1/tasks/completed/by_completion_date`, which keeps labels), minus
  the habit cards (`1st hci`, `2nd hci` incl. dated copies, `2 hci`) and
  `deferred:` records. Points = each task's `[N]`. Bare 分 logs
  (`+N @hci`) have no Todoist task and are not counted.

## Self-clearing

After the report it marks the `2 hci` card done via `tools/did/run.py "2 hci"`
(1n+ credit + Todoist close) and refreshes the dtd cache. `--no-mark` skips
that.
