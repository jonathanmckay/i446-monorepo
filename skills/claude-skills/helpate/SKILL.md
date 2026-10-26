---
name: "helpate"
description: "Estimate kcal, protein and Daily Dozen servings for a described meal, show the proposed /ate line, and log it via /ate on 'y'. Usage: /helpate <description of what you ate>"
user-invocable: true
---

# Help Me Log Food (/helpate)

A propose-then-confirm front end for `/ate`. JM describes food loosely; you
estimate the numbers, show one line, and on `y` write it with the same
`ate.py` call `/ate` uses (see `../ate/SKILL.md` for the full writer spec).

## Usage

```
/helpate <free-text description>
/helpate 卯 <description>        # optional leading branch glyph, passed through as --branch
```

Examples:
```
/helpate big handful of cashews and a bag of beef jerky
/helpate 2 slices sourdough with avocado and an egg
```

## Steps

1. **Parse.** Peel an optional leading branch glyph (`卯辰巳午未申戌`) exactly
   as `/ate` Step 1 does. Everything else is the description. If the
   description includes explicit numbers (kcal, grams, counts), treat them as
   given and never override them.

2. **Estimate.** From nutritional knowledge, infer portion sizes from the
   wording ("handful" ≈ 1 oz, "bag of jerky" ≈ 2.5–3 oz, etc.), then compute:
   - `kcal`: total, rounded to the nearest 5–10.
   - `protein`: grams, rounded to 1, consistent with the kcal.
   - `groups`: Daily Dozen servings (table in `/ate`), counted by Dr Greger's
     serving sizes (nuts: ¼ cup or 2 tbsp nut butter; berries: ½ cup; greens:
     1 cup raw; grains: ½ cup cooked or 1 slice bread; beans: ½ cup cooked;
     spices: ¼ tsp turmeric or any notable spice use = 1). Under-tag rather
     than over-tag; animal protein, oils and refined sugar map to zero.
   - `name`: a short, cleaned-up food name (fix spelling, keep it terse).

3. **Propose and stop.** Print exactly this, then end the turn waiting for a
   reply:
   ```
   <name> · ~<kcal> kcal · ~<protein>g protein · <groups or "no groups">
     assumes: <portion assumptions, one line>
   /ate <name>, <kcal>, <protein> (<groups>)
   log it? y / or correct me
   ```
   Set the terminal red first (`~/i446-monorepo/scripts/term-color.sh red`),
   since the reply is a user decision.

4. **On reply.**
   - `y` / `yes` / `ok` → run the writer exactly as `/ate` Step 2:
     ```bash
     python3 ~/i446-monorepo/tools/ate/ate.py \
       --name "<name>" --kcal "<kcal>" --protein "<protein>" \
       [--groups "<groups>"] [--branch <glyph>]
     ```
     Omit `--groups` when there are none. Echo the script's confirmation line,
     marking the fields as estimated (`~`) per `/ate` Step 4.
   - A correction (e.g. "it was 2 oz jerky", "protein 30", "no spices") →
     re-estimate with the correction applied, and re-propose (back to Step 3).
     User-stated numbers are fixed from then on.
   - `n` / `no` → drop it, write nothing.

## Notes

- Never write before an explicit yes; that is the whole point of this skill
  versus `/ate`, which estimates and writes in one shot.
- Branch is picked at write time (current hour), not at proposal time, unless
  a glyph was given.
