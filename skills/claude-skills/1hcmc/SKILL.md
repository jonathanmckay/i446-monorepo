---
name: "1hcmc"
description: "Weekly media review (the 1 hcmc card). Reports which books were started and finished during the target week (same Wed-Tue week convention as /1s897), audits media-tag credits, then asks what you learned about media consumption this week and how to do better next week, writes the answer to Neon 1分+1s!AO and closes the card. Usage: /1hcmc [M.W or YYYY-MM-DD]"
user-invocable: true
---

# Weekly Reading Review (1hcmc)

Reports book activity for a week — which books got started (`/book <title>` /
`/book started <title>`) and which got finished (`/book finished`) — sourced
straight from `hcmc/reviews/` frontmatter, no Toggl or Neon involved.

**Target week**: same resolution as `/1s897` — defaults to the most recent
complete week; an optional `M.W` fiscal-week label or `YYYY-MM-DD` picks a
different one. Reuse that skill's helper directly, do NOT hand-derive the
dates:

```bash
python3 ~/.claude/skills/1s897/week_calc.py [ARG]
```

Pass the user's argument (if any) verbatim as `[ARG]`. It prints
`week_start<TAB>week_end` (both ISO, Wed-Tue) and a `WARN:` to stderr if the
resolved week ends in the future — surface that warning if present.

## Response style

Terse. No preamble. Do the work, report results.

## Steps

### Step 1: Resolve the week

As above. State `week=Wed YYYY-MM-DD → Tue YYYY-MM-DD` before continuing.

### Step 2: Pull book activity

```bash
python3 ~/i446-monorepo/tools/hcmc/book-week-report.py <week_start> <week_end>
```

Each output line is `STARTED\t<title>\t<author>` or `FINISHED\t<title>\t<author>`.
No output at all means nothing started or finished that week — that's a
normal, reportable result, not an error.

### Step 2b: Media-tag audit (#-1 / #-2 / #-3 → 0n AV / AW / AX)

```bash
python3 ~/i446-monorepo/tools/hcmc/media-tag-audit.py <week_start> <week_end>
```

Sums the week's Toggl entries carrying an explicit value tag per local day and
compares them to the 0n minute columns the tags feed (-1 → AV, -2 → AW,
-3 → AX minus the 新闻/词汇 minutes the AX formula already folds in).
Shortcode-implied tags are ignored, same as tag_credits.py. Tag credits only
fire on `toggl_api.stop_timer`, so entries closed by a trim, a mobile retime
or a backfilled span silently earn nothing (2026-09-27: week 9.16-9.22 had
324 #-1 minutes uncredited).

- `status: missing` rows → rerun with `--fix`; it appends the shortfall via
  the tag-credit write path (src `1hcmc media-tag audit`) and never subtracts.
- `status: sheet>toggl` rows → hand-entered minutes with no tagged entry
  behind them; report them, do not change them.
- Include the table (or "media tags: all matched") in the Step 3 report.

### Step 2c: Media survey (the card's manual part, 2026-10-17)

The weekly media note lives in Neon `1分+1s` column **AO** (the "Notes" cell
beside the 新闻/词汇/-1/-2/-3/书 counts) on the row of the **current
Sunday-anchored fiscal week**, the same row `/book finished` bumps for books.

```bash
python3 ~/i446-monorepo/tools/hcmc/hcmc-survey.py --show
```

prints the target row, last week's note and anything already in this
week's cell. Put the Step 3 report and last week's note in front of the
user, then ask, in chat:

> **What did I learn about media consumption this week? How can I do better next week?**

Wait for the answer. Then write it and close the card in one go (the
script bulletizes one `• ` per line, marks `1 hcmc` done via did-fast and
refreshes the dtd cache):

```bash
python3 ~/i446-monorepo/tools/hcmc/hcmc-survey.py --from-text "<answer>" --mark
python3 ~/i446-monorepo/tools/did/agent_claims.py release >/dev/null 2>&1
```

An answer that already exists in the cell is replaced. If the user declines
to answer, write nothing and leave the card open.

### Step 3: Report

```
W <week_start> - Tu <week_end>

Started (N):
• <Title> — <Author>

Finished (M):
• <Title> — <Author>
```

Omit the Started/Finished block entirely if its count is 0 rather than
printing an empty list. If both are 0: `No books started or finished W
<week_start> - Tu <week_end>.`

## Notes

- `date:` in a book's frontmatter is set once, at `/book`/`/book started`
  time, and never changes — it's the started date. `finished:` is set by
  `/book finished`. A book can show in both buckets the same week if it was
  read start-to-finish within it.
- Apart from the Step 2c survey cell (`1分+1s!AO`) and the media-tag fix, this skill doesn't write to Neon. The weekly books-read
  counter (`1分+1s!AM`) is already incremented live by `/book finished`
  itself; nothing here needs to reconcile it.
