# XBOX ExP Weekly Dashboard — Copilot Skill

A shareable Copilot skill that pulls last week's Xbox experiment activity from
the ExP MCP server and renders it as a polished HTML dashboard.

## Sections

1. **Experiments Ready for Review** — split into "Newly ready" (14–21 days) and "Previously ready" (21+ days)
2. **Stopped last week** — with outcome badges (Promoted / Rolled out / Completed / Stopped)
3. **Started last week**
4. **Currently active**

## What it looks like

Feature-level accordion cards grouped by stage (GIA > Omega > Beta > Canary).
Each step row shows split, start/end dates, duration, decision badge, and
scorecard link. Steps running ≥14 days get a "🎯 2-week scorecard available"
sub-row indicator.

## Prerequisites

| | |
|--|--|
| **Copilot CLI or VS Code** | With GitHub Copilot Chat |
| **ExP MCP server** | `exp.azure.net/mcp` configured with `useMicrosoftCredential: true` |
| **Python** | 3.9+ on PATH. No third-party packages needed. |
| **Access** | Microsoft corpnet or Azure sign-in for ExP authentication |

## Install

Copy this folder to your Copilot user skills directory:

```powershell
# Windows
$dest = "$env:USERPROFILE\.copilot\skills\exp-weekly-dashboard"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item -Recurse -Force ".\*" $dest
```

```bash
# macOS / Linux
mkdir -p ~/.copilot/skills/exp-weekly-dashboard
cp -R ./* ~/.copilot/skills/exp-weekly-dashboard/
```

Restart your editor so Copilot picks up the new skill.

## Use it

Say one of:
- "Build the weekly experiment dashboard"
- "Show me last week's Xbox experiments"
- "What shipped this week"

### Manual usage (no Copilot)

```powershell
# Full pipeline: pull live data from ExP MCP + render HTML
python generate_live_weekly_data.py --render

# Or two-step:
python generate_live_weekly_data.py   # writes weekly_data.json
python build_weekly_cards.py          # reads JSON, writes weekly_dashboard.html
```

## Files

| File | Purpose |
|------|---------|
| `SKILL.md` | Agent runbook — step-by-step instructions for Copilot |
| `generate_live_weekly_data.py` | Live MCP-backed data generator |
| `build_weekly_cards.py` | HTML renderer (reads `weekly_data.json`, writes `weekly_dashboard.html`) |
| `weekly_dashboard.html` | **Pre-rendered dashboard** — open immediately to see current week |
| `weekly_data.json` | Current week's data — refresh with `generate_live_weekly_data.py` |
| `xbox_logo.png` | Dashboard header logo |
| `references/weekly_data.schema.md` | Full schema documentation for `weekly_data.json` |
| `examples/weekly_data.example.json` | Minimal sample data (run renderer against this without MCP) |
| `examples/weekly_dashboard.example.html` | Example rendered output |

## Quick start

1. Open `weekly_dashboard.html` in a browser — you'll see the most recent week's data
2. When you want to refresh for a new week, run:
   ```powershell
   python generate_live_weekly_data.py --render
   ```
3. That's it — the HTML file is updated in place

## Section assignment logic

A feature is placed in the **highest-priority** section where it has steps:
- `active` > `started` > `stopped`

This means a feature with both stopped and active steps goes to "Active"
(not "Stopped"). A feature only appears in "Stopped" if ALL its steps ended.

## "Ready for Review" logic

- **Newly ready**: steps running 14–21 days (just crossed the 2-week scorecard threshold this week)
- **Previously ready**: steps running 21+ days (was already past threshold last week)

Features appear in the review section AND their normal section (duplicated, not moved).
Steps that are ready get a row accent tint + "🎯 2-week scorecard available" sub-row.

## Exclusions

The dashboard automatically excludes:
- **Validation groups**: `xbox~validation~user`, `xbox~validation~device`, `xbox~xtarget`
- **Test experiments**: names starting with "test ", "a/a", "aa", or "test flight"
- **Non-AB steps**: DataCollection / feature switch steps are counted but not shown as cards

## Outcome definitions (for stopped steps)

| Outcome | Meaning | How it's detected |
|---------|---------|-------------------|
| **Promoted** | Step advanced to a new A/B step in the progression | Next step is an AB test |
| **Rolled out** | Step moved to 100% data collection (shipping) | Next step is a DC/100% step |
| **Completed** | Final rollout finished (100% DC with no successor) | Current step is 100% DC, no next step |
| **Stopped** | Experiment ended with no successor | No next step, not a 100% DC |

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Only 10 steps, all outside the week window | MCP ignoring filter (known quirk) | SKILL.md Step 1 has the fallback approach |
| 404 on Experiment or Feature query | Wrong GUID | Re-query the parent feature's experiment list |
| Empty dashboard | Quiet week (e.g., holiday) | Check section counts; they show totals before AB filtering |
| Cards show "—" for splits | Variant percentages not captured | Re-pull the step and stringify variant percentages |

## Sharing

Zip this folder and hand it to a teammate. They unzip into
`~/.copilot/skills/exp-weekly-dashboard/` — no registry, no install script.
