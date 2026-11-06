---
name: "0s"
description: "Daily social/reflection review (accrual: reviews yesterday). Opens a full-screen form (all questions at once) to fill title, gratitude, wins, scores, etc., then writes each answer to the reviewed day's row in the Neon 0s897 tab (yesterday by default; motivation to the next day). Usage: /0s [YYYY-MM-DD]"
user-invocable: true
---

# Daily Social Review (/0s)

A full-screen TUI form for the daily 0s review. All questions are on screen at
once so you fill them one after the next; on submit (`^S`) each answer is written
to the matching column of the **reviewed day's** row in the Neon `0s897` tab. 0s
is **accrual/retrospective** — you fill it the next day about the day before — so
by default it reviews **yesterday** and the main fields land in **yesterday's**
row. The forward-looking **motivation** field is written to the following day's
row (**today**, by default; col D).

## Field → column map (0s897, one row per day, date in col B as M/D/YY)

| Field | Col | Field | Col |
|-------|-----|-------|-----|
| Title of the Day | E | ⌈ ceiling | P |
| Who did I notice | F | ⌊ floor | Q |
| Gratitude of the moment | G | x̄ mean | R |
| Biggest win today | H | Proud of (others) | S |
| Learning for tomorrow | I | Learnings (others) | T |
| 霓虹 (num) | K | ⌈ ceiling (others) | V |
| 帮助 (num) | L | ⌊ floor (others) | W |
| 身体 (num) | M | Motivation → **following day** (today) row | D |
| Body Notes | N | | |

`霓虹 / 帮助 / 身体` and the `⌈ / ⌊ / x̄` fields are numbers, entered manually
(no auto-computation). Empty fields are skipped on write, so blanks never
overwrite existing cells.

The **last** field, **Points checked? (1 = mark 0l done)**, is *not* a neon
column — it's an action toggle: enter `1` and on save the form runs `did-fast 0l`
(marks the daily 0l habit done); `0` or blank leaves 0l untouched.

## Launch

The form is a full-screen prompt_toolkit TUI, so it needs its own terminal
(pass the optional date arg through if the user gave one):

```bash
~/i446-monorepo/scripts/open-tui.sh 0s "python3 ~/i446-monorepo/tools/0s/0s.py [YYYY-MM-DD]"
```

It opens a cmux tab, a tmux window on Ix, or a Terminal.app tab, whichever this
session has. Relay its output line. Exit 3 prints the line for the user to run.

Then confirm: fill the form, ^S to save to 0s897.

## Keys (inside the form)

- **Tab / Shift-Tab** — move between fields
- **^S** — save (validates the number fields, then writes Excel on Ix)
- **^Q / ^C** — cancel without writing

## Notes

- The writer routes through `~/.claude/skills/_lib/ix-osa.sh` (Excel is open on
  Ix), matching every other Neon write. Never writes a local copy.
- The reviewed-day row is found by matching its date (`M/D/YY`) in `0s897` col B.
  With no arg the reviewed day is **yesterday**; motivation goes to the following
  day's row (today). Pass `[YYYY-MM-DD]` to review a specific day.
- Non-interactive paths (for scripting/tests): `0s.py --from-json <file>` writes
  answers from JSON; `0s.py --print-script --from-json <file>` prints the
  AppleScript without writing.
