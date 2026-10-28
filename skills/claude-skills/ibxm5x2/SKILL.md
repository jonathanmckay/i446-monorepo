---
name: "ibxm5x2"
description: "m5x2 inbox pass: file the team's weekly-update emails into f695 as links (and out of the inbox), then move every email that needs no response from JM into the ai-no-response-needed label. Usage: /ibxm5x2 [--dry-run]"
user-invocable: true
---

# /ibxm5x2 — m5x2 inbox: weekly updates → f695, no-response mail → label

The m5x2 inbox is Gmail on the m5c7 account (m5x2.com mail lands there).
Read `tools/ibx/PRINCIPLES.md` once if you haven't: filing is conservative,
reversible (label + remove INBOX, never delete) and logged; when in doubt, keep.

## Run

With `--dry-run` in the arguments, pass `--dry-run` to both commands and only
report what they would do.

```bash
cd ~/i446-monorepo/tools/ibx
# 1. Weekly updates → links in f695, then out of the inbox
python3 m5x2_weekly_links.py --days 21 --archive
# 2. Everything else that needs no response → ai-no-response-needed
python3 agent_triage.py m5x2 --all
```

1. `m5x2_weekly_links.py` finds the team's weekly updates (LX, Ian, Andie,
   Stef, Leeroy, Florencia, Lawrence, Olga, Jesus, Viviana, Tim — the same matchers as the m5x2
   weekly-reports dash) and adds one link line per update to
   `~/vault/h335/f693/1-f695-weekly-updates.md` under `## m5x2 weekly updates`
   → `### Week of <Y.MM.DD>` (week received). Already-linked threads are
   skipped. `--archive` then files each linked update still in the inbox.
   Last line of output is JSON: `found`, `linked`, `filed`.
2. `agent_triage.py m5x2 --all` classifies every inbox message (read or
   unread) with `claude -p` against the ibx criteria and files the confident
   "no response needed" ones. Each filed message is logged to
   `~/.config/ibx/agent_filtered.jsonl` (undo = re-add INBOX). A run that's
   already in progress (the ibx m5x2 timer hook) makes this a no-op.

## Report

Two lines:

```
f695 ← N weekly-update links (who, …); M filed out of the inbox
inbox → K of T filed to ai-no-response-needed; T−K left for you
```

Then list the subjects the triage filed (from its `skip:` lines) so a wrong
call is visible at a glance. Don't open, reply to, or summarize email bodies.
