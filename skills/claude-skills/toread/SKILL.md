---
name: "toread"
description: "Use when JM runs /toread, or asks to put a link, article, essay, paper or an existing dtd task on JM Reads ('make it a card in JM reads', 'file this to read', 'add to my reading list'). Takes a URL, a dtd row number, a task name or id, or a plain 'Title — Author'. Not for a book he has started or finished (/book), not for writing a review (/bookreview), not for a new chore that is not reading (/todo)."
user-invocable: true
---

# To Read (/toread)

Files a card on the **want to read** shelf of JM Reads (personal dashboard,
`/jmreads`). The shelf is the table in `~/vault/hcmc/to-read.md`; a row whose
title cell reads `Title — Author` is a card, linked when it has a link.

## Response style

One line, no explanation, no confirmation question:

```
📚 <Title> — <Author> → JM Reads
```

Add `, task deleted` when a Todoist task was removed.

## Run

Pass JM's argument through as typed (quote it as one argument):

```bash
python3 ~/i446-monorepo/tools/hcmc/toread-add.py "<argument>" [--author X] [--area CODE]
```

The script decides what the argument is:

| Argument | Result |
|---|---|
| contains a URL | that link; title and byline are read from the page. Words typed beside the URL are the title (`<url> Title — Author` sets both) |
| a dtd code (`ii`, `业`) | the task whose dtd row leads with that code |
| a bare number | the task on that dtd row |
| a task id or words of a task name | that task: its name, first link, `(N)` minutes, `[N]` 分 and domain label |
| `Title — Author`, no task match | a card with no link |

`--area` overrides the Area cell (default `hcmc`, or the task's label).
`--author` overrides the byline when the page gave a poor one.

## Read the output

- `+ <Title> — <Author> → JM Reads (to read) <url>`: filed; the dashboard is
  refreshed in the background.
- `task: <id>` on the next line: the card came from a Todoist task. **Delete
  that task** (`mcp__todoist__delete-object`, type `task`), since the shelf now
  tracks it (JM, 2026-11-15). Delete, never complete: reading it is not done,
  so no points are logged and /did is not run. Skip the delete only if the
  task is recurring; say so instead.
- `EXISTS: <row>`: already on the shelf; report it, add nothing. If it came
  from a task, still delete the task.
- `ERROR: no fresh dtd row N`: the row numbers are from a dtd render on
  another host or over an hour old. Ask JM for a few words of the task name,
  or re-run with the name if he already gave one. Never guess a nearby row.
- `ERROR: ambiguous task: ...`: show the candidates and ask which.
- `ERROR: no dtd task matches`: the words are neither a task nor
  `Title — Author`; ask for the link or the author.

## Check the row number against what JM said

A dtd row number is whatever this host's latest render shows, which can
differ from the screen JM is looking at. When he names the task as well as
the number ("task 19, the AI Futures thing"), run with `--dry-run` first and
confirm the title matches his words; if it does not, use the words instead of
the number.

```bash
python3 ~/i446-monorepo/tools/hcmc/toread-add.py 19 --dry-run
# | 1 i447 — i447 | ...        <- not the AI Futures task: wrong row
python3 ~/i446-monorepo/tools/hcmc/toread-add.py "AI Futures"
# + AI Futures Q3.5 timelines update — Eli Lifland → JM Reads (to read) https://...
# task: 6hxW9VJWJVcwHch6       <- delete this Todoist task
```

## Finishing a card

Reading it is tracked in the table: set the row's Link cell to `Done` and the
card leaves the shelf. A book that gets started moves to `/book started`.
