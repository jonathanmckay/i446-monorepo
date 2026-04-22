---
name: "ate"
description: "Log a food entry to today's hcbi row, into the time-of-day triad picked by current hour (Earthly Branches 卯辰巳午未申戌). Usage: /ate <name>, <kcal>, <servings> [(group count, ...)]"
user-invocable: true
---

# Log Food Eaten (/ate)

Append a food entry to today's row in the `hcbi` sheet of `Neon分v12.2.xlsx`,
into the cell triad whose Earthly Branch covers the current hour.

## Usage

```
/ate <name>, <kcal>, <servings> [(<group> <count>, ...)]
```

Examples:
```
/ate raspberries, 80, 1 (berries 3)
/ate oatmeal with flax, 350, 2 (grains 1, flax 1)
/ate salad, 200, 1 (greens 2, cruciferous 1, vegetables 1)
/ate 1 cup oat milk, 120, 1
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

Each branch maps to a triad of columns in `hcbi`. Triad = (food name, kcal, servings).

| Hours       | Branch | Cols       |
|-------------|--------|------------|
| 04:00–07:59 | 卯     | AL, AM, AN |
| 08:00–09:59 | 辰     | AO, AP, AQ |
| 10:00–11:59 | 巳     | AR, AS, AT |
| 12:00–13:59 | 午     | AU, AV, AW |
| 14:00–15:59 | 未     | AX, AY, AZ |
| 16:00–17:59 | 申     | BA, BB, BC |
| 18:00–03:59 | 戌     | BD, BE, BF |

00:00–03:59 also lands in 戌 (still considered "today's" row).

## Behavior

- **Food name** (col 1 of triad): if cell is empty, set; else append `", " + name`.
- **Kcal** (col 2): if empty, set to the number; else convert to formula `=<old>+<kcal>`.
- **Servings** (col 3): same formula-append logic.
- **Date row**: matched by `M/D` in column `B` of `hcbi`.
- **Row 1 labels**: every invocation idempotently writes the seven branch glyphs
  to `AL1, AO1, AR1, AU1, AX1, BA1, BD1` and clears the other two header cells
  in each triad. Existing labels (Early Morning / Breakfast / …) get overwritten
  on first run.

## Steps

1. **Parse args.** Split user input on the **last two commas** (so the name may
   itself contain commas):
   ```python
   name, kcal, srv = [s.strip() for s in user_input.rsplit(",", 2)]
   ```
   Validate that `kcal` and `srv` parse as numbers. If not, ask the user to
   reformat.

2. **Run the writer.** Excel must be open with `Neon分v12.2.xlsx` loaded.
   ```bash
   python3 ~/i446-monorepo/scripts/neon-ate.py "<name>" <kcal> <srv>
   ```
   Optional flags: `--date M/D` to backfill, `--hour H` to force a band.

3. **Report.** Echo the script's one-line confirmation, e.g.:
   ```
   ate raspberries (80 kcal, 1 srv) → hcbi 巳 band (AR/AS/AT), row 113
   ```

## Failure modes

- `ERR:date_not_found` → today's date isn't in `hcbi` col B yet. Ask user
  whether to add the row manually first or use `--date` to target an existing
  row.
- AppleScript error mentioning `workbook` → Neon isn't open. Open it and retry.
