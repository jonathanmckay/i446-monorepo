---
name: "moralaudit"
description: "Quarterly moral audit: a full-screen form covering Stewardship (Capital, Org/People, Property, Relationships, Time, 自) x Externalities (Individual, Group) against i9/m5x2/个, each cell a free-text reflection plus a 1-7/n/a rating. Writes a dated vault note with a colored HTML grid (color from rating) and opens it. Usage: /moralaudit [YYYY-MM-DD]"
user-invocable: true
---

# Moral Audit (/moralaudit)

A full-screen prompt_toolkit form for the quarterly moral audit — same
skeleton as `/0s`/`/1s`/`/xk887`. Every (row, column) cell gets a free-text
reflection plus a 1-7 rating (or `n/a`); the rating sets that cell's fill
color in the rendered grid. On submit, writes a dated note to
`~/vault/hcmp/moral-audit/YYYY-MM-DD-moral-audit.md` with an inline-styled
HTML table (renders natively in Obsidian) and opens it.

## Design (settled 2026-08-13, do not re-litigate without JM)

- **Rows are NOT split into competence vs. morality.** They're one fused
  question per Aristotle (*no one is practically wise without being good, nor
  good without practical wisdom* — NE VI.13). Splitting them lets technical
  skill launder a bad outcome ("I ran it efficiently" standing in for "should
  I have run it at all") — the Eichmann/banality-of-evil failure mode. Ask
  each cell as one holistic judgment that already includes "for whom."
- **Stewardship rows** (resource types JM controls):
  - `capital` — Money: deployed productively, or sitting idle?
  - `org` — Org/People: people he has AUTHORITY over (employees, culture)
  - `property` — Physical space/property he controls
  - `relationships` — People he has NO authority over (reciprocity, not
    control — distinct from `org`)
  - `time` — His own hours: allocation across what's possible
  - `zi` (自) — Attention/energy quality. NOT the same as `time` — `time` is
    allocation, `自` is capacity/quality of the self doing the allocating.
- **Externalities rows** — orthogonal to Stewardship (you can steward
  perfectly and still spray externalities onto people outside the
  relationship): `ext_individual`, `ext_group`. Single rating each — no
  competence axis; "competence at generating externalities" isn't coherent.
- **Columns**: `i9` (Microsoft/work), `m5x2` (McKay Capital), `个`
  (personal/self). Not every cell need apply — `n/a` is a first-class answer,
  not a gap, so the form accepts it everywhere.
- **Stewardship's "occupy a city block and do nothing" case is why
  Displacement became Stewardship**: underuse of a controlled resource is
  itself the moral question (Georgist idle-land critique / EA opportunity
  cost of capital), not a separate economic-vs-moral split.

The full row/column taxonomy, and the reasoning above, all came out of a
design conversation with JM on 2026-08-13 — see that day's conversation if
this doc needs to change. Don't silently rename or resplit rows without
checking with him; the naming is load-bearing (e.g. "Org/People" vs
"Relationships" hinges specifically on authority vs. no-authority).

## Launch

Same pattern as `/0s` — the form is full-screen, needs its own terminal:

1. Open a new cmux surface:
   ```bash
   cmux new-surface --type terminal
   ```
2. Run the form in that pane (optional date arg, default today):
   ```bash
   cmux respawn-pane --surface surface:<N> --command "python3 ~/i446-monorepo/tools/moralaudit/moralaudit.py [YYYY-MM-DD]"
   ```
3. Focus it: `cmux focus-pane --pane pane:<N>`
4. Confirm: `moralaudit opened in a new cmux tab — fill the grid, last field saves.`

If cmux is unavailable, tell the user to run it themselves:
`! python3 ~/i446-monorepo/tools/moralaudit/moralaudit.py`

## Keys (inside the form)

- **Tab / Shift-Tab** — move between fields
- **Enter** — newline in a text cell; advances on a rating field; saves on the
  last field (autosave-on-finish, same convention as `/0s`/`/xk887` — no
  separate save keystroke required to finish naturally)
- **^S** — save from anywhere
- **^Q / ^C** — cancel without writing

Rating fields are validated on submit: must be `1`-`7`, `n/a`, or blank
(blank = treated as n/a). Invalid entries block save with an inline message
naming the offending cells.

## Output

- Path: `~/vault/hcmp/moral-audit/YYYY-MM-DD-moral-audit.md` (hcmp — this
  ritual's actual journal home; JM has been writing "moral audit" entries
  under `hcmp/o314/` since 2011, and a standalone `hcmp/Morals + Audit.md`
  predates this skill. Moved 2026-08-13 from an initial `g245/5e4/` guess.)
- Frontmatter: `title: "Moral Audit YYYY-Qn"`, `date`, `type: moral-audit`,
  `tags: [hcmp]`
- Body: one HTML `<table>`, dark theme, section header rows for Stewardship /
  Externalities, row labels in a left column, cell background interpolated
  red (1) → gray (4) → green (7); `n/a`/blank cells render near-black with
  dim text (matches the aesthetic of JM's original reference screenshot,
  `z_asts/Pasted image 20260407134038.png`)
- Opened via `obsidian://open?path=<urlencoded>` after write (per standing
  convention — `open -a Obsidian` silently fails on special-char filenames)

## Non-interactive paths (for scripting/tests)

- `moralaudit.py --from-json <file>` — write answers from JSON, skip the form
- `moralaudit.py --print-html --from-json <file>` — print the HTML table only,
  no write

## Notes

- No Excel/Neon write at all — this is a vault-markdown skill, not a Neon
  ritual. It doesn't correspond to a dtd card, so it isn't self-clearing.
- On a local write failure, answers are dumped to
  `~/.cache/moralaudit-recovery/<timestamp>.json` rather than lost.
- Cadence is quarterly by convention (matches the historical "5^4 Moral
  Audit" ritual) but the script itself doesn't enforce a schedule — it just
  runs when invoked, dated to `[YYYY-MM-DD]` or today.
