---
name: "1-1n"
description: "Generate the weekly -1₦ block-ritual heatmap: a day-by-block table showing which build-order ritual icons (☀️ prayer, 📧 inbox, 🎯 goal/points, ⏱️ time-log, ✓ task done) were hit in each 2-hour Earthly-Branch block. Pulls from Toggl, the Neon 0分 spreadsheet, and Todoist, then writes a report to vault/g245 and opens it. Usage: /1-1n [START END]"
---

# -1₦ Block Ritual Heatmap (/1-1n)

Builds a `Date × 2-hour block` table of build-order ritual completion to see how
the week went on the icons that accompany every block of the build order.

## Execution

Run the generator. With no args it covers the trailing 7 days (ending yesterday):

```bash
python3 ~/.copilot/skills/1-1n/make_heatmap.py            # last 7 days
python3 ~/.copilot/skills/1-1n/make_heatmap.py 2026-06-01 2026-06-10   # explicit range
```

Then write the output to the vault with frontmatter and open it:

```bash
OUT=~/vault/g245/$(date +%Y.%m.%d)-block-ritual-heatmap.md
{
  printf -- '---\ntitle: "-1₦ Block Ritual Heatmap"\ndate: %s\ntype: report\ntags: [g245, neon, -1n, rituals]\n---\n\n' "$(date +%Y-%m-%d)"
  python3 ~/.copilot/skills/1-1n/make_heatmap.py <START> <END>
} > "$OUT"
open "obsidian://open?vault=vault&file=g245%2F$(basename "$OUT")&newTab=true"
```

Optionally append a short Commentary section (phase shifts, weakest rituals, week-over-week density). Keep it factual and brief.

## Icons (the build-order rituals)

| Symbol | Ritual | Source |
|--------|--------|--------|
| ☀️ | prayer (salah) | Toggl: الفاتحة / الشمس (project hcm) by start time |
| 📧 | inbox processed | Toggl: description starts with `ibx` |
| 🎯 | goal set / points (-1g) | Neon `0分` per-block columns G:O (卯..亥), nonzero |
| ⏱️ | time logged | Toggl: `-1t` / `-1l` / `0t` / `0l` |
| ✓ | task completed | Todoist completed tasks (`/api/v1/tasks/completed`) by completed_at |

## Blocks (地支, −1h shift, 04:00 wake)

`卯` 06-08, `辰` 08-10, `巳` 10-12, `午` 12-14, `未` 14-16, `申` 16-18, `酉` 18-20, `戌` 20-22, `亥` 22-00. Sleep blocks (寅/子/丑) are omitted.

## Notes

- Times are bucketed in America/Los_Angeles (PDT, −07:00). Adjust `PT` in the script for PST.
- Toggl key loads via `~/i446-monorepo/mcp/toggl_server/toggl_cli.py`; Neon path is `~/OneDrive/vault-excel/Neon分v12.2.xlsx` (read-only open is safe even while Excel has it open); Todoist token is embedded.
- If a source fails, the script still renders and appends an HTML comment with the warning.
- The report is a sibling of the prior `g245/YYYY.MM.DD-block-ritual-heatmap.md` files. Each run writes a new dated file.
