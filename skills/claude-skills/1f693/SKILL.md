---
name: "1f693"
description: "Weekly f693 people stoplight (the '1 f693' card). Opens the drawio org diagram in the m5x2 Chrome context, then a 6-slot survey of 'initials + what I need to do so they succeed'; each slot becomes a Todoist task (f693 INIT: ... (N) [N], labels i9+f693) and a line in h335/f693/f693.md, which also gets the pasted org screenshot. '/1f693 stoplight' reads the pasted screenshot and writes the stoplight table. Usage: /1f693 | /1f693 stoplight"
user-invocable: true
---

# Weekly f693 stoplight (/1f693)

The ritual (defined in `g245/5e1/1-neon-meta.md` › Specific Rituals › f693):
**stoplight every person on the org diagram, then add a todo for what I need
to do to make each of them more successful.** Two halves:

1. **Survey** (`/1f693`, or Enter on the `1 f693` card in dtd — same form):
   opens the diagram, collects up to 6 todos, files them, marks the habit done.
2. **Stoplight** (`/1f693 stoplight`): after the screenshot is pasted into the
   doc, Claude reads it and writes the stoplight table.

The form script is `~/i446-monorepo/tools/f693/1f693.py`; it does everything
in half 1 itself (Todoist, doc entry, `did run.py "1 f693"`, Obsidian, and it
opens a cc tab running `/1f693 stoplight` so half 2 is waiting).

## `/1f693` (no args)

Same launch pattern as `/0s` — the form needs its own terminal:

1. `cmux new-surface --type terminal` → parse `surface:N` / `pane:N`.
2. `cmux respawn-pane --surface surface:N --command "python3 ~/i446-monorepo/tools/f693/1f693.py"`
3. `cmux focus-pane --pane pane:N`
4. Confirm in one line: `1 f693 opened in a new cmux tab — diagram is in Chrome; fill the 6 slots, Esc saves.`

If cmux is unavailable: `! python3 ~/i446-monorepo/tools/f693/1f693.py`.

Do not create the Todoist tasks or edit the doc yourself in this mode — the
form does it. Do not mark `1 f693` done yourself either (the form calls
`did run.py "1 f693"`; the claim hook's keeper stays until the user closes it).

## `/1f693 stoplight`

Runs after the user pasted the diagram screenshot into
`vault/h335/f693/f693.md` › `## Current Org` › today's entry (the form left a
`<!-- paste the org screenshot here … -->` comment and a
`**Stoplight:** _pending …_` line).

1. Find the screenshot: the newest `~/vault/z_asts/Pasted image *.png`
   (`ls -t ~/vault/z_asts/Pasted\ image*.png | head -1`). If it is older than
   ~2 hours, or the doc's entry has no `![[Pasted image …]]` embed yet, say
   so and wait — do not guess from an old image. Read the image.
2. Translate the diagram into a table. One row per person box, in diagram
   order (top-down, left-right), using the box colour as the status:

   | Person | Status | Note |
   |---|---|---|
   | AB (role) | 🟢 | one phrase on why, if the box says anything |

   Colour legend (from the doc's own key): 🟢 green = everything good;
   🟡 yellow = needs attention / pipeline going well, nobody identified;
   🟠 orange = pipeline not going well; 🔴 red = at risk / blocked;
   ⚪ grey/white = TBH / to start. Keep names as initials + role when the
   box shows a role; never invent people not in the image.
3. Edit today's entry in place: replace the `**Stoplight:** _pending …_` line
   with `**Stoplight:**` followed by the table; delete the paste-here comment
   if the embed is present. Leave the `**Next steps:**` list untouched.
4. Open the doc (`open "obsidian://open?path=h335%2Ff693%2Ff693.md"`) and
   confirm with the row count and any boxes you could not read.

## Notes

- Todos default to `(15) [10]`; a slot can carry its own `(N)` / `[N]`.
- Labels `i9` + `f693` (f693 is the Microsoft org; the 1 f693 habit scores
  into 0分 col AA = i9).
- Diagram link, pinned in the doc and in the script:
  https://app.diagrams.net/#G13lL3iRagUQucleRmp966RMG8R2iMRoUd%23%7B%22pageId%22%3A%22Fk-glKlrqk2ZiSOgV627%22%7D
  opened with `open -na "Google Chrome" --args --profile-directory="Profile 11"`
  (the m5c7.com profile; there is no m5x2.com Chrome profile).
