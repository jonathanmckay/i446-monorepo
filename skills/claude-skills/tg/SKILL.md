---
name: "tg"
description: "Quick Toggl time tracking with auto-tagging. Starts/stops/creates entries using shortcode-to-project mapping."
user-invocable: true
---

# Toggl Quick Entry (/tg)

Fast Toggl time tracking. Auto-maps descriptions to projects and tags.

## Execution

**Always run `tg-fast.py` first.** Do NOT reason about shortcodes; the script handles all resolution.

```bash
python3 ~/i446-monorepo/tools/tg/tg-fast.py "<all args verbatim>"
```

Echo the script's output to the user. Done. No additional processing needed.

The script handles: shortcode→project mapping, @overrides, time ranges, backdated starts, stop/today/current/delete.

## Response Style

**Minimal output.** Echo the script result. One line. Examples:
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
| `<HHMM> <desc>` | Start running timer with backdated start time (see below) |
| `del <id>` | Delete entry by ID |

### Backdated start

When input contains a single 4-digit time (HHMM) with no dash/range, start a running timer whose start time is backdated to that time today. Steps:

1. Stop any currently running timer.
2. If the stopped timer overlaps the backdated time (its start is before, end/now is after), trim it: delete the old entry and recreate it ending at 1 minute before the backdated time using `python3 $CLI create <desc> <old_start> <backdate-1min> <project>`.
3. Start a new timer with the backdated start time: `python3 $CLI start <desc> <project> --at HH:MM`

Example: `/tg 1823 o314` at 18:45 stops the current timer, trims it to end at 18:22, and starts a running o314 timer from 18:23.

### Time formats accepted
- `9-10` or `9-10am` → 9:00 AM to 10:00 AM
- `9:30-10:15` → exact times
- `21:00-22:30` → 24h format
- `2h` or `90m` → duration (create entry ending now)
- `1823` (single 4-digit, no dash) → backdated running timer starting at 18:23

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

Use the Toggl CLI script via Bash:

```
CLI=~/i446-monorepo/mcp/toggl_server/toggl_cli.py
python3 $CLI start <description> [project_code] [tag1 tag2 ...]
python3 $CLI stop
python3 $CLI current
python3 $CLI today
python3 $CLI create <description> <HH:MM|HHMM> <HH:MM|HHMM> [project_code] [--date YYYY-MM-DD]
python3 $CLI delete <entry_id>
```

Pass **project codes** (e.g., `g245`, `i9`, `m5x2`) directly — the CLI resolves them to IDs automatically.

For **tags**, pass them as additional positional args after the project code on `start`.

## Day barrier rule

Never create a time entry that crosses midnight. If a time range spans midnight, split it into two entries: one ending at 23:59 and one starting at 00:00. The CLI handles this automatically in `create`.
