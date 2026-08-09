---
name: "xk887"
description: "Weekly family/marriage review, similar to /0s. Opens a full-screen paginated form (one page per person) covering the xk88 (marriage/social), xk20 (Theo), xk22 (Ren), and xk26 (Rori) tabs of xk887.xlsx; each page is written to the review week's row as it is submitted. Usage: /xk887 [M.W or YYYY-MM-DD]"
user-invocable: true
---

# Weekly Family Review (/xk887)

A full-screen TUI form for the weekly xk887 review — same interaction
pattern as `/0s`, but weekly instead of daily and spanning **four sheets**,
one page per person: `xk88` (marriage/social), `xk20` (Theo), `xk22` (Ren),
`xk26` (Rori), all in `xk887.xlsx`. Each page is **written to Excel the
moment it's submitted** (Enter/Tab past the last field, or `^S`), so a
cancel or crash on a later page never loses earlier pages. Rows target the
**review week** (default: the last completed Sun–Sat week, same convention
as `/1s`). Fields sit on adjacent lines with no blank rows; multiline boxes
grow with content instead of reserving empty height.

## Field → sheet/column map

Rows are keyed by col A's **M.W label** (Sunday-anchored: `M` = the
Sunday's month, `W` = which Sunday of that month it is — e.g. Sun 7/19 →
`7.3`). This is the same convention `1分+1s` uses.

### xk88 · Marriage / Social

| Field | Col | Field | Col |
|-------|-----|-------|-----|
| Good | B | Upcoming | G |
| Regrettable | C | Did / Notes | H |
| Focus | D | What did I do that was kind | J |
| Notes | E | Best husband this week? | K |

### xk20 (Theo) / xk22 (Ren) — same columns, different framing text

| Field | Col |
|-------|-----|
| He/She is | B |
| New / Curriculum | C |
| Concerned / Opportunity | D |
| Well | E |
| Better | F |
| Notes | G |
| Goals | H |

### xk26 (Rori) — same as above, plus:

| Field | Col |
|-------|-----|
| Age (weeks), numeric | B |
| New / Concerned / Well / Better / Notes / Goals | C–H |

All fields are free text except xk26's **Age (weeks)**, which is numeric.
Empty fields are skipped on write, so blanks never clobber existing cells —
**except** Age: if left blank on a newly-appended week row, it
auto-continues from the previous week's age + 1 (matching the sheet's own
established convention of `=prev+1` formulas that harden into literals).

## Row lookup vs. append (important difference from `/1s`)

`1分+1s` pre-populates future weeks in advance, so `/1s` only ever looks a
row up. **These four sheets do not** — col A is a formula chain
(`=prev+0.1`, rolling to `=prev+1` at month-end) that hardens into a
literal once a row is filled, and the row after the last one is genuinely
blank. So the tool:

1. Scans col A (via **string value**, not `value` — the formula chain has
   float-precision display artifacts like `6.199999999999999` for `6.2`,
   same lesson `0s.py` already learned for its date column) for the review
   week's label, independently per sheet.
2. If found, writes into that row.
3. If not found, **appends a new row** right after the sheet's last
   populated row, writing the M.W label as a literal.

Each of the four sheets is resolved independently, so it's fine if one
sheet is a week ahead/behind another.

## Launch

The form is a full-screen prompt_toolkit TUI — open it in a new cmux tab
(same pattern as `/0s` / `/1s`):

1. Open a new cmux surface:
   ```bash
   cmux new-surface --type terminal
   ```
   Parse the surface and pane refs from the output.
2. Run the form in that pane (pass the optional week arg through if given —
   either an `M.W` label like `7.4` or any ISO date in the week):
   ```bash
   cmux respawn-pane --surface surface:<N> --command "python3 ~/i446-monorepo/tools/xk887/xk887-survey.py [M.W|YYYY-MM-DD]"
   ```
3. Focus it:
   ```bash
   cmux focus-pane --pane pane:<N>
   ```
4. Confirm: `xk887 opened in a new cmux tab — fill the form, ^S to save.`

If cmux is unavailable, tell the user to run it themselves:
`! python3 ~/i446-monorepo/tools/xk887/xk887-survey.py`

## Keys (inside the form)

- **Tab / Enter** — next field; on a page's **last** field, saves the page
  (writes that sheet to Excel on Ix) and advances to the next person
- **Shift-Tab** — previous field; on a page's **first** field, back one page
  (nothing written; re-submitting an already-written page is safe — blanks
  never clobber)
- **^→ / ^PageDown** — save the current page (same as Tab/Enter on the last
  field) and jump to the next page, from **any** field, not just the last
- **^← / ^PageUp** — back one page, from **any** field, not just the first
  (nothing written)
- **^S** — save the current page now (validates Age is numeric) and advance
- **^Q / ^C** — cancel; pages already submitted stay written

## Notes

- The writer routes through `~/.claude/skills/_lib/ix-osa.sh` (Excel is
  open on Ix). Never writes a local copy.
- No Todoist task is marked done on save. The existing weekly `1 xk88 (5)
  [15]` Todoist habit tracks TIME spent (a variable-duration `/did` habit,
  distinct from survey completion) and is unaffected — unlike `/1s`, where
  submitting the survey IS completing that week's task.
- Non-interactive paths (scripting/tests): `xk887-survey.py --from-json
  <file>` writes answers from JSON; `--print-script --from-json <file>`
  prints the AppleScript without writing.
- `xk887.xlsx` (same file `/3xk87` writes to, on its `3轩轩`/`3琪琪`/`3熙熙`
  sheets — this skill only touches `xk88`/`xk20`/`xk22`/`xk26`) is opened on
  Ix automatically if it isn't already (2026-08-04 — it isn't kept open like
  `Neon分v12.2.xlsx`, and drifts closed between infrequent runs; a bare
  `workbook "xk887.xlsx"` reference used to throw uncaught and crash the
  whole form the instant a page was submitted, silently losing whatever was
  typed). If a write still fails for any other reason, the page's answers
  are dumped to `~/.cache/xk887-recovery/` and reported with a plain error
  instead of crashing — replay with `--from-json <path>`.
