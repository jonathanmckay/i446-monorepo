---
name: "tg"
description: "Quick Toggl time tracking with auto-tagging. Starts/stops/creates entries using shortcode-to-project mapping."
user-invocable: true
---

# Toggl Quick Entry (/tg)

Fast Toggl time tracking. Auto-maps descriptions to projects and tags.

## Response Style

**Minimal output.** After executing, confirm in one line. Examples:
- `Started: 0l → g245`
- `Stopped: work (42min)`
- `Created: 睡觉 22:00–06:00 → 睡觉 [-3]`
- `Today: 6h 23min across 14 entries`

Do NOT explain what you're doing. Do NOT ask for confirmation. Just execute.

## Commands

Parse the user's input after `/tg`:

| Pattern | Action |
|---------|--------|
| `<shortcode>` | Start timer with mapped project/tags |
| `stop` | Stop the running timer |
| `today` | Show today's entries |
| `current` | Show what's currently running |
| `<desc> <start>-<end>` | Create completed entry with time range |
| `<desc> <start>-<end> @<project>` | Create entry with explicit project override |
| `del <id>` | Delete entry by ID |

### Time formats accepted
- `9-10` or `9-10am` → 9:00 AM to 10:00 AM
- `9:30-10:15` → exact times
- `21:00-22:30` → 24h format
- `2h` or `90m` → duration (create entry ending now)

## Shortcode → Project/Tag Mapping

When the description matches a shortcode below, auto-assign the project and tags. Match is **case-insensitive** and **exact** (not substring).

### High frequency (10+ uses)

| Shortcode | Project | Tags | Notes |
|-----------|---------|------|-------|
| `الفاتحة` | hcm | | |
| `睡觉` | 睡觉 | -3 | |
| `fall asleep` | hcmc | -1 | |
| `0t` | n156 | | |
| `新闻` | hcmc | -3 | |
| `work` | i9 | | |
| `family time` | xk87 | | |
| `read` | xk87 | -3 | |
| `0l` | g245 | | |
| `math` | xk87 | | |
| `冥想` | hcm | | |
| `day hci` | hci | | |
| `wake up` | infra | | |
| `bball` | hcbp | | |
| `其他人` | hcm | | |
| `-1l` | g245 | | |
| `o314` | hcm | -3 | |
| `hiit` | hcbp | -2 | |
| `vibing` | i9 | | |
| `hcmr` | hcm | | |
| `kn47 daily` | m5x2 | | |
| `0g` | g245 | | |
| `epcn` | epcn | | |
| `h lunch` | hcb | | |
| `meetings` | i9 | | |
| `tasks` | i9 | | default; override with @m5x2 etc |
| `ren to sleep` | xk87 | | |
| `1s` | g245 | | |
| `get up` | infra | | |
| `词汇` | hcmc | -3 | |
| `doze` | hcmc | -1 | |
| `youtube` | hcmc2 | 2 | |
| `stats` | i9 | | |
| `out the door` | infra | | |

### Medium frequency (3–9 uses)

| Shortcode | Project | Tags |
|-----------|---------|------|
| `h breakfast` | hcb | |
| `breakfast` | hcb | |
| `dinner` | xk87 | |
| `lunch` | hcb | |
| `h dinner` | hcb | |
| `dad call` | 家 | |
| `lx walk` | xk88 | |
| `r203 Weekly` | m5x2 | |
| `r202 Weekly` | m5x2 | |
| `kids to sleep` | xk87 | |
| `lego` | xk87 | |
| `notes` | i9 | |
| `-1t` | n156 | |
| `starcraft` | hcmc2 | 2 |
| `IM\|JM 1\|1` | m5x2 | |
| `الشمس` | hcm | |
| `news` | hcmc | -3 |
| `teams` | i9 | |
| `m5x2 People` | m5x2 | |
| `m5x2 Strat (1\|1\|1)` | m5x2 | |
| `return home` | xk87 | |
| `bio` | infra | |
| `lx chat` | xk88 | |
| `lx call` | xk88 | |
| `mom call` | s897 | |
| `1 hcme` | hcm | |
| `day` | hci | |
| `snack` | hcb | |
| `m5x2 Accounting & Analytics` | m5x2 | |
| `SLT` | i9 | |
| `slt` | i9 | |
| `exp meeting` | i9 | |
| `w225 + l912 weekly` | m5x2 | |
| `coffee` | epcn | |
| `stuart call` | s897 | |
| `family breakfast` | xk87 | |
| `family dinner` | xk87 | |
| `weekly update` | i9 | |
| `f693` | i9 | |
| `shower` | hci | |
| `SLT prep` | i9 | |
| `-1g` | g245 | |
| `النور` | hcm | |
| `pack` | i444 | |
| `1 xk87` | xk87 | |
| `1 -1n` | g245 | |
| `ana 1\|1` | i9 | |
| `1 -2g` | g245 | |
| `to uber` | i444 | |
| `lx checkin` | xk88 | |
| `metrics meeting` | i9 | |
| `carolina 1\|1` | i9 | |
| `fix computer` | i9 | |
| `through airport` | i444 | |
| `ibx` | m5x2 | |
| `plan weekend` | xk87 | |
| `hospital time` | xk87 | |
| `generic placeholder` | infra | |
| `unsure` | infra | |

### Domain shortcodes (just a project code)

If the input is ONLY a known project code with no description, start a timer with that project and no description:

| Code | Project |
|------|---------|
| `hcm` | hcm |
| `hcmc` | hcmc |
| `hcb` | hcb |
| `hcbp` | hcbp |
| `hci` | hci |
| `i9` | i9 |
| `m5x2` | m5x2 |
| `xk87` | xk87 |
| `xk88` | xk88 |
| `s897` | s897 |
| `epcn` | epcn |
| `g245` | g245 |
| `n156` | n156 |
| `i444` | i444 |
| `infra` | infra |
| `家` | 家 |
| `睡觉` | 睡觉 |

## Override with @

If the user appends `@<project>` to any input, that overrides the mapped project. Strip the `@code` from the description.

Examples:
- `/tg tasks @m5x2` → description "tasks", project m5x2 (not default i9)
- `/tg ibx @i9` → description "ibx", project i9 (not default m5x2)
- `/tg random thing @hcmc` → description "random thing", project hcmc

## Passthrough

If the description doesn't match any shortcode and has no `@project`, **start the timer with just the description and no project**. The user can always add `@code` to assign one.

## Tools

Use the Toggl MCP server tools:
- `toggl_start` — start a timer (description, project code)
- `toggl_stop` — stop the running timer
- `toggl_create_entry` — create a completed entry (description, project, start, end)
- `toggl_current` — get the running timer
- `toggl_today` — list today's entries
- `toggl_delete` — delete an entry by ID

Pass **project codes** (e.g., `g245`, `i9`, `m5x2`) directly — the Toggl MCP server resolves them to IDs automatically.

For **tags**, pass tag names as a list in the `tags` parameter.

## Day barrier rule

Never create a time entry that crosses midnight. If a time range spans midnight, split it into two entries: one ending at 23:59 and one starting at 00:00. The Toggl MCP server enforces this automatically.
