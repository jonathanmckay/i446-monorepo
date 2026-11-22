---
name: "-1l"
description: "Use when JM runs /-1l, opens the 😈 -1l block card, or asks to sweep today's time log and close the block ('fill my holes', 'log the day so far', 'do -1l'). Not for ranges he names or another day (/auton156), not for a finished day (/0t --check, /0t fill), not for the monthly audit (/2n), not for starting or stopping a timer (/tg, /do)."
user-invocable: true
---

# Block log sweep (/-1l)

`/auton156` with no ranges, then the `😈 -1l` card. The evidence, the fill
order and the entry rules are all `/auton156`'s. Two things differ here
(JM 2026-11-22: "if I call -1l and there are blank spots on the cal, fill
them in, without affecting other time entries"):

- **Every blank is a hole**, down to one whole minute, not only the 5-minute
  ones janus flashes. Find them with `evidence.py --min-gap 1`.
- **New entries only.** A blank is filled by `toggl_create_entry` inside it.
  Never extend, move, rename or delete an entry that exists to close a gap
  (placeholders stay the one exception, as in `/auton156`). So a 1-3 minute
  seam gets its own short entry, which overrides `/auton156`'s "one entry per
  5-10 minutes" for these; a blank with no evidence still stays empty and is
  named in the report.

```
/-1l              # today, 00:00 to now
/-1l dry          # report holes and proposed fills, write nothing
```

## Steps

1. **sweep.** Do `/auton156` bare (its steps 1-4), for today, with the holes
   from `python3 ~/i446-monorepo/tools/auton156/evidence.py --min-gap 1`. On
   `dry`, stop before `toggl_create_entry` and report the proposed table.
2. **check_tasks_pointed.** From the `find-activity` completions the sweep
   already fetched (fetch them if it stopped at `no holes`), list any real
   task (not a habit, not a `😈` ritual, not `@posthoc`) closed in the
   previous 2-hour block with no `[N]` or `{N}`. This is the card's own
   criterion.
3. **close_card.** Only when no hole of 5m or more (janus's flash threshold) remains in the previous block
   and step 2 found nothing:
   ```bash
   python3 ~/i446-monorepo/tools/did/did-fast.py --ritual -1l >/dev/null 2>&1
   python3 ~/i446-monorepo/tools/did/did-fast.py --refresh-cache
   ```
   Otherwise leave the card open and say which condition failed. Never on
   `dry`. Skip it outside 04:00-22:00 (no block is open) and when the
   completions already show this block's `😈 -1l` closed.

## Report

`/auton156`'s table and `Left empty:` line, then:

```
Unpointed: none.
😈 -1l closed.
```
