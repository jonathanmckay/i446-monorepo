---
name: "-1l"
description: "Use when JM runs /-1l, opens the 😈 -1l block card, or asks to sweep today's time log and close the block ('fill my holes', 'log the day so far', 'do -1l'). Not for ranges he names or another day (/auton156), not for a finished day (/0t --check, /0t fill), not for the monthly audit (/2n), not for starting or stopping a timer (/tg, /do)."
user-invocable: true
---

# Block log sweep (/-1l)

`/auton156` with no ranges, then the `😈 -1l` card. The hole finding, the
evidence, the fill order and the entry rules are all `/auton156`'s; nothing
about them is restated or changed here.

```
/-1l              # today, 00:00 to now
/-1l dry          # report holes and proposed fills, write nothing
```

## Steps

1. **sweep.** Do `/auton156` bare (its steps 1-4), for today. On `dry`, stop
   before `toggl_create_entry` and report the proposed table.
2. **check_tasks_pointed.** From the `find-activity` completions the sweep
   already fetched (fetch them if it stopped at `no holes`), list any real
   task (not a habit, not a `😈` ritual, not `@posthoc`) closed in the
   previous 2-hour block with no `[N]` or `{N}`. This is the card's own
   criterion.
3. **close_card.** Only when no hole over 5m remains in the previous block
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
