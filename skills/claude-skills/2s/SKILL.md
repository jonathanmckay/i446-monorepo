---
name: "2s"
description: "Copy a month's Neon sums (1分+1s M.1 row) into the 计分卡 2s,3s scorecard (18-26 2s tab). Usage: /2s [month] [--year YYYY]"
user-invocable: true
---

# Monthly Scorecard Paste (/2s)

Copy one month's aggregate sums from Neon into the 计分卡 scorecard — the
mechanical paste step of the monthly 2s review.

## Execution

Run the helper with the args verbatim and echo its one-line output:

```bash
python3 ~/i446-monorepo/tools/2s/2s-fast.py [month] [--year YYYY] [--dry-run]
```

`[month]` accepts `4`, `april`, `apr`, or `2026-04`; omitted → the **previous
calendar month** (it's a month-end review). Year defaults to the current year.
`--dry-run` prints the resolved rows + values without writing.

## What it does

- **Source:** `Neon分v12.2.xlsx` (open in Excel on **Ix**), sheet `1分+1s`.
  The month's aggregates live on its **first-week row** (col A == `M.1`, e.g.
  April = 4.1 = row 15). Read live via ix-osa so formula values are current,
  not save-cache stale.
- **Target:** `计分卡  2s, 3s.xlsx` (Dropbox `Eclipse SSD`, open in **local**
  Excel on Straylight — Dropbox is NOT synced to Ix, so this is the one Excel
  write that is deliberately local; the OneDrive-conflict rule doesn't apply).
  Sheet `18-26 2s`; row looked up by col A == year, col B == month, never
  hardcoded (April 2026 → row 101). Opens the workbook from Dropbox if closed;
  saves after writing.
- **Mapping** (header-verified 2026-07-05): `AQ:BA` → `C:N` **skipping I**
  (deprecated 5^1₦ stays empty), and `BC` (分/d) → `Y`. Values pasted as
  literals; re-runs are idempotent refreshes.

| src | AQ | AR | AS | AT | AU | AV | AW | AX | AY | AZ | BA | BC |
|-----|----|----|----|----|----|----|----|----|----|----|----|----|
| dst | C  | D  | E  | F  | G  | H  | J  | K  | L  | M  | N  | Y  |

## Failure modes

- `no M.1 row` — 1分+1s has no week-1 row for that month yet.
- `month not aggregated yet` — the M.1 row exists but AQ:BC are all empty.
- Write error — 计分卡 not open and the Dropbox path moved; check
  `~/Library/CloudStorage/Dropbox/Eclipse SSD/计分卡  2s, 3s.xlsx`.
- Ix unreachable — the source read fails loudly (never falls back to the stale
  on-disk copy).

## Notes

- **Current-year only.** `1分+1s` holds the current tracking year (col A has no
  year). A January run (previous month = December) is a year-boundary edge
  case — confirm the source row is really that month before trusting it.
- **Scope.** Quantitative row only. The qualitative sheet (`18-24 2s qual`) and
  quarterly `16-23 3s` stay manual, as does the 2s Meta reflection in
  `≥1 ₦ Neon 长期霓虹系统 (Rituals).md`.
- **Predecessor retired.** `copy2s.py` (2026-07-03, formerly in this folder)
  targeted the STALE OneDrive `scorecard.xlsx` (`18-25 2s` tab) and lacked the
  `BC→Y` paste — do not resurrect it.
- When the tab rolls over (`18-27 2s`…), update `SHEET_2S` in `2s-fast.py`.
