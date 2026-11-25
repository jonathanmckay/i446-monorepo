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
# 3. Refresh the ✉N badges on the ibx m5x2 and 😈 -1ibx dtd cards (skip on --dry-run)
python3 ibx0.py --counts
```

1. `m5x2_weekly_links.py` finds the team's weekly updates (LX, Ian, Andie,
   Stef, Leeroy, Florencia, Lawrence, Olga, Jesus, Viviana, Tim — the same matchers as the m5x2
   weekly-reports dash) and adds one link line per update to
   `~/vault/h335/f693/1-f695-weekly-updates.md` under `## m5x2 weekly updates`
   → `### Week of <Y.MM.DD>` (week received). Already-linked threads are
   skipped. `--archive` then files each linked update still in the inbox.
   Last line of output is JSON: `found`, `linked`, `filed`.
2. `agent_triage.py m5x2 --all` takes every inbox message from the last 30
   days (read or unread; `--days N` changes the window). Fixed sender rules
   file first (`RULE_SKIP`: all Property Meld notifications, JM 2026-11-15);
   the rest is classified 20 at a time with `claude -p` against the ibx
   criteria, and the confident "no response needed" ones are filed. It logs
   a `keep:` line with the reason for everything it leaves. A kept message is
   remembered (`~/.config/ibx/agent_kept.json`) and not sent to the agent
   again, so a run only pays for new mail; `--recheck` ignores that cache, and
   editing the criteria text drops it. Each agent call is logged with its cost
   to `~/.config/ibx/agent_cost.jsonl` (`agent_triage.py cost` prints the
   per-day totals). Each filed message is logged to
   `~/.config/ibx/agent_filtered.jsonl` (undo = re-add INBOX). A run that's
   already in progress (the ibx m5x2 timer hook) makes this a no-op.
3. `ibx0.py --counts` recounts what is still pending and rewrites
   `~/vault/z_ibx/inbox-counts-<host>.json`, so the `ibx m5x2` card in dtd shows
   the post-triage `✉N` now instead of at the next 5-minute launchd tick. It
   prints JSON; N is `habits["ibx m5x2"].count` (`"skipped": "unchanged"` means
   the badge was already right). The 😈 -1ibx ritual card is the sum of the
   `ibx s897`, `ibx m5x2` and `slack m5x2` badges from that same file, so it
   moves with this write; the JSON's `-1ibx` field is what it shows now (`e13 s2` = 13 email, 2 Slack; `""`
   = blank, because one of the three failed its ping). It never closes the card. If `ok` is false the
   Gmail ping failed and dtd shows no badge: say so instead of quoting a count.

## Report

Four lines:

```
f695 ← N weekly-update links (who, …); M filed out of the inbox
inbox → K of T filed to ai-no-response-needed; T−K left for you
dtd ← ibx m5x2 ✉N · -1ibx eE sS
cost ← $X.XX this run · $Y.YY last 30d
```

The cost line is always present (JM 2026-11-25). This run's figure is the
`agent cost $X.XX` at the end of the triage's `filed K of T` log line; the
script omits it when no agent call was made (everything rule-filed or already
in the kept cache), so report `$0.00` then. The 30-day figure is the
`last 30d:` line of `python3 agent_triage.py cost`. Actual dollars, so `$` is
right here.

Then list the subjects the triage filed (from its `skip:` lines) so a wrong
call is visible at a glance; give rule-filed mail as one line with its count
(e.g. "81 Property Meld notifications"), not one line each. Don't open, reply to, or summarize email bodies.
