---
name: "ate"
description: "Log a food entry to today's hcbi row, into the time-of-day triad picked by current hour (Earthly Branches 卯辰巳午未申戌). Any of kcal/protein/food-groups you omit gets filled in with a best estimate. Usage: /ate <name>[, <kcal>[, <protein_g>]] [(group count, ...)]"
user-invocable: true
---

# Log Food Eaten (/ate)

Append a food entry to today's row in the `hcbi` sheet of `Neon分v12.2.xlsx`,
into the cell triad whose Earthly Branch covers the current hour.

## Excel host

All Excel writes go through **Ix** (Mac Mini, Tailscale) — Neon is open there, not on Straylight. Wrap every `osascript` call:

```bash
ssh ix 'osascript <<APPLESCRIPT
tell application "Microsoft Excel"
  ...
end tell
APPLESCRIPT'
```

If `ssh ix` fails (timeout/unreachable), fall back to local `osascript` and warn the user — this is a degraded state. Set terminal orange via `~/i446-monorepo/scripts/term-color.sh orange`.

## Usage

```
/ate [<branch>] <name>[, <kcal>[, <protein_g>]] [(<group> <count>, ...)]
```

Optional leading **branch glyph** (one of 卯辰巳午未申戌) forces the time band
instead of picking by the current hour. Still lands in today's row.

**Any of `<kcal>`, `<protein_g>`, or the `(<group> ...)` groups can be omitted** —
whatever's missing gets filled in with a best-effort estimate from nutritional
knowledge of the named food (portion size inferred from the name, e.g. "2 eggs"
vs "1 egg"), not asked for. A field the user DID type is never overridden.

Examples:
```
/ate raspberries, 80, 1 (berries 3)          # fully specified, nothing estimated
/ate 2 eggs and toast                        # kcal, protein, groups all estimated
/ate chicken caesar salad, 450                # kcal given; protein + groups estimated
/ate oatmeal with flax (grains 1, flax 1)     # groups given; kcal/protein estimated
/ate 卯 leftover oatmeal, 300, 1     # force the 卯 (early morning) band
```

### Nutrition groups (Daily Dozen)

When parenthesized groups are present, increment those columns in `hcbi` for today's row.

| Name        | Abbrev | Column |
|-------------|--------|--------|
| beans       | bn     | E      |
| berries     | br     | F      |
| fruit       | fr     | G      |
| cruciferous | cr     | H      |
| greens      | gr     | I      |
| vegetables  | vg     | J      |
| flax        | fx     | K      |
| grains      | g      | L      |
| nuts        | nt     | M      |
| spices      | sp     | N      |
| water       | wtr    | O      |

Pass to the script as `--groups abbrev:count ...`, e.g. `--groups br:3 g:1`.

## Time bands (Earthly Branches)

Each branch maps to a triad of columns in `hcbi`. Triad = (food name, kcal, protein grams). Column P aggregates the protein triad cells via =SUM — never append to P directly; write protein into the band's third column only.

| Hours       | Branch | Cols       |
|-------------|--------|------------|
| 04:00–07:59 | 卯     | AK, AL, AM |
| 08:00–09:59 | 辰     | AN, AO, AP |
| 10:00–11:59 | 巳     | AQ, AR, AS |
| 12:00–13:59 | 午     | AT, AU, AV |
| 14:00–15:59 | 未     | AW, AX, AY |
| 16:00–17:59 | 申     | AZ, BA, BB |
| 18:00–03:59 | 戌     | BC, BD, BE |

00:00–03:59 also lands in 戌 (still considered "today's" row).

## Behavior

- **Food name** (col 1 of triad): if cell is empty, set; else append `", " + name`.
- **Kcal** (col 2): if empty, set to the number; else convert to formula `=<old>+<kcal>`.
- **Protein g** (col 3): same formula-append logic.
- **Date row**: matched by `M/D` in column `B` of `hcbi`.
- **Row 1 labels**: every invocation idempotently writes the seven branch glyphs
  to `AK1, AN1, AQ1, AT1, AW1, AZ1, BC1` and clears the other two header cells
  in each triad. Existing labels (Early Morning / Breakfast / …) get overwritten
  on first run.

## Tracking points (0s / col T)

Every `/ate` call recomputes today's food-tracking score from total tracked
calories (`hcbi!U`, header `cal` — a live `=SUM` over all seven band kcal
cells) and bumps `hcbi!T` (header `0s`) if the tier increased:

| Condition                  | Points |
|-----------------------------|--------|
| Tracked anything (>0 kcal)  | 10     |
| Tracked >800 kcal            | +5     |
| Tracked >1200 kcal           | +5     |
| **Max**                      | **20** |

`T` is a plain literal (not a formula) that already flows into `hcbi!AA` →
`0分!W` (the `hcb` domain column) via existing formulas — no separate `0分`
write is needed. The write only fires when the new tier exceeds the current
`T` value, so repeated `/ate` calls never double-count and a manually-set
higher value (e.g. a one-off bonus) is never clobbered downward.

## Steps

1. **Parse args.** First, peel off an optional leading **branch glyph**: if the
   input begins with one of `卯辰巳午未申戌` followed by whitespace, strip it and
   remember it as `forced_branch`; otherwise `forced_branch = None`.
   ```python
   BRANCHES = "卯辰巳午未申戌"
   forced_branch = None
   parts = user_input.strip().split(None, 1)
   if parts and parts[0] in BRANCHES:
       forced_branch, user_input = parts[0], parts[1]
   ```
   Next, peel off a trailing parenthesized groups list if present (e.g.
   `(berries 3)`, `(grains 1, flax 1)`) and strip it from the remainder:
   ```python
   import re
   groups_match = re.search(r'\(([^)]*)\)\s*$', user_input)
   groups_str = groups_match.group(1) if groups_match else None
   if groups_match:
       user_input = user_input[:groups_match.start()].rstrip()
   ```
   Then split the remainder on the **last two commas** (so the name may itself
   contain commas), and classify how many TRAILING segments are actually
   numeric — `kcal`/`protein` are always the last one or two comma-separated
   fields when given, but either or both may be absent entirely:
   ```python
   segments = [s.strip() for s in user_input.rsplit(",", 2)]

   def is_numeric(s):
       try:
           eval(s, {"__builtins__": {}})  # arithmetic like 300+200+60 is fine
           return True
       except Exception:
           return False

   trailing = []
   while segments and len(trailing) < 2 and is_numeric(segments[-1]):
       trailing.insert(0, segments.pop())

   name = ",".join(segments).strip()
   kcal    = trailing[0] if len(trailing) >= 1 else None
   protein = trailing[1] if len(trailing) >= 2 else None
   ```
   `kcal`/`protein` end up `None` exactly when the user didn't type them —
   evaluate any arithmetic expression that IS present (e.g. `300+200+60`).
   `groups_str` is `None` when no parenthesized groups were given.

1b. **Estimate whatever the user left out.** Do this yourself from nutritional
   knowledge of `name` — never ask the user to fill in numbers you can
   reasonably estimate, and never override a value the user actually typed.
   - **`kcal` is `None`** → estimate total calories for the named food/portion
     (infer serving size from the name itself — "2 eggs" vs "1 egg" vs a bare
     "eggs" defaulting to a typical single serving). Round to the nearest 5–10
     kcal; don't fabricate false precision.
   - **`protein` is `None`** → estimate grams of protein for the same
     food/portion, consistent with the kcal estimate (a food that's mostly
     carbs/fat should get a correspondingly low protein estimate). Round to
     the nearest 1g.
   - **`groups_str` is `None`** → infer plausible Daily Dozen groups (the
     table below) from the food itself, using the same `abbrev:count` shape
     the explicit syntax uses. Only tag a group with genuine confidence —
     animal proteins, oils, refined grains, etc. often map to ZERO groups,
     and that's the correct answer, not a gap to force-fill. When in doubt,
     under-tag rather than over-tag.
   Track which of the three you estimated (vs. user-supplied) — Step 4's
   report calls them out explicitly so the log stays honest about what's a
   measurement and what's a guess.

   Whether `groups_str` came from the user or from your own estimate here,
   parse it into the `(abbrev, count)` pairs Step 2 writes — split on commas,
   split each piece on the last space into `(name_or_abbrev, count)`, and map
   full names to abbreviations via the table above (e.g. `"berries 3"` →
   `("br", 3)`; already-abbreviated input like `"br 3"` passes through
   unchanged). An empty/absent `groups_str` (including a genuine zero-group
   estimate) just means `groups = []` — no column writes for that entry.

2. **Run the writer.** Excel must be open with `Neon分v12.2.xlsx` loaded on **ix**. Use the `neon.excel` client (which routes through the excel-http daemon on ix at `localhost:9876`, falling back to `ssh ix osascript` if the daemon is down):

   ```python
   import sys; sys.path.insert(0, "/Users/mckay/i446-monorepo/lib")
   from datetime import datetime
   from neon import excel, cols

   today = f"{datetime.now().month}/{datetime.now().day}"
   band  = (cols.hcbi_band_by_branch(forced_branch) if forced_branch
            else cols.hcbi_band(datetime.now().hour))  # → {branch, cols: [name, kcal, srv]}
   name_col, kcal_col, protein_col = band["cols"]

   excel.append("hcbi", name_col, date=today, value=", " + name)   # name col uses comma-append
   excel.append("hcbi", kcal_col, date=today, value=f"+{kcal}")
   excel.append("hcbi", protein_col,  date=today, value=f"+{protein}")
   for abbrev, count in groups:                         # e.g. ("br", 3)
       excel.append("hcbi", cols.daily_dozen_col(abbrev), date=today, value=f"+{count}")
   ```

   Note: the **name column** is a string append, not arithmetic — first write should be plain (no leading "+"); the daemon's /append handles that automatically (sets `=name` then concats `, name` thereafter). For correctness, special-case empty-cell vs concat in the caller, or use `/write` for empties.

3. **Update tracking points.** Recompute today's food-tracking tier from the
   post-write calorie total and bump `hcbi!T` if it increased (see
   [Tracking points](#tracking-points-0s--col-t) above):

   ```python
   cal_col = cols.col("hcbi", "cal")   # U
   pts_col = cols.col("hcbi", "0s")    # T

   cal_today = float(excel.read("hcbi", cal_col, date=today)["value"] or 0)
   tier = 0 if cal_today <= 0 else 10 + (5 if cal_today > 800 else 0) + (5 if cal_today > 1200 else 0)

   current_pts = float(excel.read("hcbi", pts_col, date=today)["value"] or 0)
   if tier > current_pts:
       excel.write("hcbi", pts_col, date=today, value=str(tier), src="ate-tier")
   ```

4. **Report.** Echo the script's one-line confirmation, e.g.:
   ```
   ate raspberries (80 kcal, 2g protein) → hcbi 巳 band (AQ/AR/AS), row 113
   ```
   If step 3 bumped the tier, mention it: `+5 tracking pts (now 15/20)`.
   If anything from step 1b was estimated rather than user-supplied, flag it
   inline with `~` and a trailing note, e.g.:
   ```
   ate 2 eggs and toast (~140 kcal, ~13g protein, ~grains 1) → hcbi 辰 band (AN/AO/AP), row 113
   (kcal/protein/groups estimated — say the actual numbers to correct)
   ```

## Failure modes

- `ERR:date_not_found` → today's date isn't in `hcbi` col B yet. Ask user
  whether to add the row manually first or use `--date` to target an existing
  row.
- AppleScript error mentioning `workbook` → Neon isn't open. Open it and retry.
