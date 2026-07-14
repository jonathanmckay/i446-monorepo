---
name: "0s"
description: "Daily social/reflection review. Opens a full-screen form (all questions at once) to fill title, gratitude, wins, scores, etc., then writes each answer to today's row in the Neon 0s897 tab. Usage: /0s [YYYY-MM-DD]"
user-invocable: true
---

# Daily Social Review (/0s)

A full-screen TUI form for the daily 0s review. All questions are on screen at
once so you fill them one after the next; on submit (`^S`) each answer is written
to the matching column of **today's** row in the Neon `0s897` tab. The
forward-looking **motivation** field is written to **tomorrow's** row (col D).

## Field → column map (0s897, one row per day, date in col B as M/D/YY)

| Field | Col | Field | Col |
|-------|-----|-------|-----|
| Title of the Day | E | ⌈ ceiling | P |
| Who did I notice | F | ⌊ floor | Q |
| What am I thankful for? | G | x̄ mean | R |
| Biggest win today | H | Proud of (others) | S |
| Learning for tomorrow | I | Learnings (others) | T |
| 霓虹 (num) | K | ⌈ ceiling (others) | V |
| 帮助 (num) | L | ⌊ floor (others) | W |
| 身体 (num) | M | Motivation → **tomorrow** row | D |
| Body Notes | N | | |

`霓虹 / 帮助 / 身体` and the `⌈ / ⌊ / x̄` fields are numbers, entered manually
(no auto-computation). Empty fields are skipped on write, so blanks never
overwrite existing cells.

## Launch

The form is a full-screen prompt_toolkit TUI, so it needs its own terminal —
open it in a new cmux tab (same pattern as `/ibx`):

1. Open a new cmux surface:
   ```bash
   cmux new-surface --type terminal
   ```
   Parse the surface and pane refs from the output (e.g. `OK surface:6 pane:3 workspace:1`).
2. Run the form in that pane (pass the optional date arg through if the user gave one):
   ```bash
   cmux respawn-pane --surface surface:<N> --command "python3 ~/i446-monorepo/tools/0s/0s.py [YYYY-MM-DD]"
   ```
3. Focus it:
   ```bash
   cmux focus-pane --pane pane:<N>
   ```
4. Confirm: `0s opened in a new cmux tab — fill the form, ^S to save to 0s897.`

If cmux is unavailable, tell the user to run it themselves:
`! python3 ~/i446-monorepo/tools/0s/0s.py`

## Keys (inside the form)

- **Tab / Shift-Tab** — move between fields
- **^S** — save (validates the number fields, then writes Excel on Ix)
- **^Q / ^C** — cancel without writing

## Notes

- The writer routes through `~/.claude/skills/_lib/ix-osa.sh` (Excel is open on
  Ix), matching every other Neon write. Never writes a local copy.
- Row is found by matching today's date (`M/D/YY`) in `0s897` col B; motivation
  goes to the next day's row.
- Non-interactive paths (for scripting/tests): `0s.py --from-json <file>` writes
  answers from JSON; `0s.py --print-script --from-json <file>` prints the
  AppleScript without writing.
