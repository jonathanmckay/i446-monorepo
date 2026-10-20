---
name: "3xk87"
description: "Log PTC / teacher feedback to xk887 quarterly progress spreadsheet. Usage: /3xk87 <child> <quarter> <teacher d359 slug>"
user-invocable: true
---

# Log PTC Feedback (/3xk87)

Parse teacher conference notes from a d359 doc and populate the xk887.xlsx quarterly progress spreadsheet.

## Usage

```
/3xk87 <child> <quarter> <teacher d359 slug>
```

- `<child>`: `theo` or `ren`
- `<quarter>`: e.g. `Q1 2026`, `Q2 2026`
- `<teacher d359 slug>`: filename stem in d359, e.g. `彭老师`

### Examples

```
/3xk87 ren Q1 2026 彭老师
/3xk87 theo Q2 2026 彭老师
```

## Spreadsheet

**File:** `~/OneDrive/vault-excel/xk887.xlsx` (must be open on Ix)

### Sheet mapping

| Child | Sheet | Header row | Year row | Data start row |
|-------|-------|-----------|----------|---------------|
| Theo  | 3轩轩 | 2         | 3        | 4             |
| Ren   | 3琪琪 | 1         | 2        | 3             |

### Column mapping (quarters)

Columns E–H = Year 1 (Q1–Q4), I–L = Year 2 (Q1–Q4), M–P = Year 3 (Q1–Q4), etc.

To find the target column:
1. Read the year row to find which column the target year starts at.
2. Offset by quarter (Q1=+0, Q2=+1, Q3=+2, Q4=+3).
3. If the year isn't present yet, add it in the next available 4-column block and write the Q1–Q4 headers.

### Row mapping (3轩轩 / Theo)

| Row | Label | Category |
|-----|-------|----------|
| 4   | Teacher Feedback (general/academic) | General |
| 5   | Teacher Feedback (social/friends) | General |
| 6   | Ashan Feedback | General |
| 8   | PE / Gross motor | hcb |
| 9   | Fine motor coordination | hcb |
| 10  | Attention / Grit | hcm |
| 11  | Self control of feelings | hcm |
| 12  | Self comforting | hcm |
| 13  | Self esteem | hcm |
| 14  | Executive Function / Risk taking | hcm |
| 15  | Social emotional understanding | 家/s897 |
| 16  | Relationship with adults | 家/s897 |
| 17  | Peer relationships | 家/s897 |
| 19  | Chinese (中文) | 学习 |
| 20  | English (英文) | 学习 |

### Row mapping (3琪琪 / Ren)

| Row | Label | Category |
|-----|-------|----------|
| 3   | Teacher Feedback (general/academic) | General |
| 4   | Teacher Feedback (social/friends) | General |
| 5   | Ashan Feedback | General |
| 7   | PE / Gross motor | hcb |
| 8   | Fine motor coordination | hcb |
| 9   | Attention / Grit | hcm |
| 10  | Self control of feelings | hcm |
| 11  | Self comforting | hcm |
| 12  | Self esteem | hcm |
| 13  | Executive Function / Risk taking | hcm |
| 14  | Social emotional understanding | 家/s897 |
| 15  | Relationship with adults | 家/s897 |
| 16  | Peer relationships | 家/s897 |
| 18  | Chinese (中文) | 学习 |
| 19  | English (英文) | 学习 |

## Steps

1. **Read the d359 doc.** Find the most recent date header matching the target quarter. Extract the teacher's notes.

2. **Categorize the notes.** Map each observation to the appropriate row:
   - Academic progress, creation, general classroom → Teacher Feedback (general)
   - Friend dynamics, playdates, social coaching → Teacher Feedback (social)
   - Physical activity, motor skills → PE / Fine motor rows
   - Attention, focus, group participation → Attention / Grit
   - Emotions, jealousy, tantrums, self-regulation → Self control / Self comforting
   - Confidence, comparative behavior → Self esteem
   - Risk-taking, independence → Executive Function
   - Peer interactions, friend recommendations → Social / Peer relationships
   - Language, reading, writing → Chinese / English rows

3. **Find the target column.** Read the year row to locate the year, offset by quarter.

4. **Write to xk887.** Pipe each AppleScript through
   `~/.claude/skills/_lib/ix-osa.sh` (which runs it on Ix). Pin the
   workbook by name (`workbook "xk887.xlsx"`), never `active workbook`.
   Keep entries concise (1-3 sentences per cell). Mix English and
   Chinese as appropriate to the source material.

5. **Report.** List which rows were populated.

## Notes

- All Excel writes go through `~/.claude/skills/_lib/ix-osa.sh`. The
  helper hard-fails with exit code 3 if Ix is unreachable; do NOT
  fall back to local `osascript` — local writes cause OneDrive merge
  conflicts against the canonical workbook on Ix.
- xk887.xlsx must be open on Ix. If not, run
  `ssh ix 'open "~/OneDrive/vault-excel/xk887.xlsx"'` first.
- Do NOT overwrite existing cell values. If a cell already has
  content, append with newline separator.
- Keep cell text concise — these are summary bullets, not transcripts.
