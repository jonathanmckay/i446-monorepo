---
name: "1hcme"
description: "Weekly emotional check-in (the 1 hcme card). Shows the week's daily 0s survey rows from Neon 0s897 in the terminal with the same cell colors as Excel, asks what you learned about your emotions this week, writes the answer to the 1 hcme column (X) of the week's Thursday row, and marks the 1 hcme habit done. Usage: /1hcme [YYYY-MM-DD or M.W]"
user-invocable: true
---

# Weekly Emotional Check-in (/1hcme)

The `1 hcme` 1neon card (Thursday, 30分, hcm domain). Everything lives in the
Neon `0s897` tab: the daily 0s survey rows are the input, and column **X**
(`1 hcme`) of the week's **Thursday row** is where every weekly check-in has
been written since 1/1/26 (col A's M.W label on that row names the week).

`tools/1hcme/1hcme.py` does the whole thing in one go:

1. One Ix round trip reads the week's rows (values **and** `display format`
   fill/font colors, so conditional formatting comes through) for the window
   **Thursday → Wednesday**: the previous check-in's Thursday through the
   day before the target Thursday, clipped to today. The previous Thursday's own check-in text is shown first.
2. Renders one **table row per day** with truecolor cells painted in their
   Excel colors: day, then the numbers (⌈ ⌊ x̄ 他⌈ 他⌊ 霓 帮 身 ₹), then
   text columns in priority order (Title, Win, Learning, Thankful, Proud·o,
   Learn·o, Motivation, Noticed, Body). Each text cell is cut to `--cell N`
   characters (default 24, ellipsis on the cut); as many text columns as fit
   the terminal width are shown and the rest are named in a footer. `--cols
   N` caps the text columns. White fills render as terminal default; blank
   cells are `·`. 3. Asks: **What did I learn this week about my emotions that I should
   remember?** One thought per line; Enter on an empty line or `Ctrl+S` saves
   and exits (`^D` too); `Ctrl+C` or an empty answer aborts without writing.
4. Writes the answer (one `• ` bullet per line, matching the existing cells)
   to `0s897!X{thursday row}` via `ix-osa.sh`, then marks the habit done
   through the normal `/did` path (`tools/did/run.py "1 hcme"` → 1n+ col X
   credit + Todoist close), followed by a foreground `did-fast.py
   --refresh-cache` so an open dtd reloads. On success the cmux tab closes
   itself; on failure the pane stays open and the answer is dumped to
   `~/.cache/1hcme-recovery/<date>.txt` for `--from-text` replay.

## Which week

- No arg → the most recent Thursday on/before today. If that row's X is
  already filled, the next Thursday (the open card) is targeted when its row
  exists.
- `YYYY-MM-DD` → the Thursday of the Sun–Sat week containing that date.
- `M.W` → looked up in `0s897` col A (Thursday row with that label). Note
  this sheet labels the week containing Jan 1 as `1.1`, one week ahead of the
  fiscal labels `/1s` and `/1s897` compute, so the label is resolved against
  the sheet, never calculated.

If the target row already has an X value it is printed under "already
filled" and a new answer **replaces** it.

## Launch

It needs its own terminal for the colors and the prompt — open it in a new
cmux tab (same pattern as `/0s` and `/1s`):

1. ```bash
   cmux new-surface --type terminal
   ```
   Parse the surface and pane refs from the output.
2. ```bash
   cmux respawn-pane --surface surface:<N> --command "python3 ~/i446-monorepo/tools/1hcme/1hcme.py [week]"
   ```
   Pass the user's week argument through verbatim if given.
3. ```bash
   cmux focus-pane --pane pane:<N>
   ```
4. Confirm: `1hcme opened in a new cmux tab — read the week, answer the question, blank line to save.`

Do not block on the form. If cmux is unavailable, tell the user to run it
themselves: `! python3 ~/i446-monorepo/tools/1hcme/1hcme.py`

Then release the dtd claim the `/1hcme` prompt took, if any (no-op otherwise):

```bash
python3 ~/i446-monorepo/tools/did/agent_claims.py release >/dev/null 2>&1
```

## From dtd

Enter on the `1 hcme` card runs the script inline in the dtd pane (the
survey-card form map in `tools/did/dtd.sh`, alongside 0l, 1s, xk887, moral
audit, -1g): timer starts, the week renders, the answer is written and the
card closed, then dtd returns. The script skips its cmux tab-close when
`DTD_HDR_FILE` is set, so it never closes dtd's own pane.

## Flags (scripting / tests)

- `--print-context` — render only; no question, no write, no mark.
- `--from-text "…"` — non-interactive answer (still writes + marks; pane not closed).
- `--print-script --from-text "…"` — print the write AppleScript (`HCME_ROW` env sets the row).
- `--no-mark` — write X but leave the `1 hcme` card open.
- `--today YYYY-MM-DD` — override today for tests.
- `--cell N` / `--cols N` — text cell width / max text columns.

## Notes

- All Excel reads/writes go through `~/.claude/skills/_lib/ix-osa.sh`; never
  local `osascript`.
- The write is a plain `set value` on one cell plus `save`; nothing else in
  the row is touched, so the daily 0s answers are never clobbered.
- Self-clearing: finishing the check-in IS completing the `1 hcme` card (see
  README roster).
