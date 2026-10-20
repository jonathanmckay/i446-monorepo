---
name: "stale-contacts"
description: "Scan d359 contacts for overdue outreach based on cadence. Creates Todoist tasks. Usage: /stale-contacts"
user-invocable: true
---

# Stale Contacts Sweep (/stale-contacts)

Scan ~/vault/d359/ for contacts with overdue outreach cadence, then create Todoist reminder tasks.

## Primary path (use this)

```bash
python3 ~/i446-monorepo/scripts/stale-contacts.py --apply   # omit --apply to dry-run
```

The script is the single implementation: it refreshes `last_contact` from Toggl/d358, scans every d359 file for overdue `cadence`+`last_contact`, dedupes against open `d359/<slug>`-labelled tasks, and creates the tasks. It also runs daily via launchd (`com.mckay.stale-contacts`). Echo its output.

**Conventions the script enforces (keep manual edits consistent):**
- Every auto-generated task is prefixed **😈** — the marker for "created by a daemon/process, not by hand." This applies to ALL automation that writes to Todoist, not only this sweep.
- A d359 file may set `outreach_task: "call mom (20) [20]"` to override the default `Reach out to <Name> (overdue …)` body.
- Tasks are labelled `["<rollup>", "d359/<slug>"]` so time/points roll up to the contact. `<rollup>` is the contact's domain: a whitelisted tag (家 for family, xk87/xk88 social) from the d359 file's `tags:`, else `s897` (the default for all work/career contacts). See `ROLLUP_DOMAINS` in the script to divert more domains.

The steps below are reference for the logic the script implements.

## Response Style

Terse. No preamble. Run the sweep, report results.

## Steps

### Step 0: Get current date

Run `date +%Y-%m-%d` to get today's date. Do not rely on context variables.

### Step 0.5: Auto-refresh last_contact

Before scanning, run the passive-signal refresher so `last_contact` reflects recent Toggl entries, d358 meeting notes, and Google Calendar meetings (manual values are preserved as a floor):

```bash
python3 ~/i446-monorepo/tools/d359/refresh_last_contact.py --days 90 --apply
```

This cuts false-positive overdues from stale manual `last_contact` fields. Run with `--days 30` for a quick daily refresh; `--days 90` for a weekly catch-up. Without `--apply`, it dry-runs. Pass `--no-calendar` to skip the Google Calendar pull (offline runs).

**Calendar signal (high-precision by design):** the MSFT (Slow Sync) ICS import strips attendees, so work 1:1s are matched by the `<INITIALS>:JM` title convention (e.g. `JA:JM 1:1` → Jessica Allen) via an unambiguous-initials map; shared-initials contacts match nobody. Primary-calendar events also match by `work_email`/`teams_upn` attendance. Personal `email:` fields and generic title tokens are deliberately ignored to avoid bumping contacts from family events and org-wide OOF/standup noise. Uses the google-calendar-mcp OAuth token (`~/.config/google-calendar-mcp/tokens.json`); degrades to no-op if unavailable.

### Step 1: Scan d359 files

Read all `.md` files in `~/vault/d359/`. For each file, parse YAML frontmatter and check for **both** `cadence` and `last_contact` fields. Skip files missing either field. Skip `CLAUDE.md`, `d359-index.md`, and any non-person docs.

### Step 2: Evaluate overdue status

For each contact with both fields, calculate days since `last_contact` relative to today.

**Cadence thresholds** (days before flagging as overdue):

| Cadence       | Threshold |
|---------------|-----------|
| weekly        | 10        |
| monthly       | 38        |
| quarterly     | 100       |
| semi-annual   | 200       |
| annual        | 400       |

A contact is **overdue** if `days_since_last_contact > threshold`.

### Step 3: Create Todoist tasks for overdue contacts

For each overdue contact, create a Todoist task using the `add-tasks` MCP tool:

- **content**: `Reach out to [Name] (overdue [cadence]: last contact [date])`
  - `[Name]` = the `title` from frontmatter (or filename if no title)
  - `[cadence]` = the cadence value (e.g., "monthly")
  - `[date]` = the `last_contact` date
- **labels**: `["<rollup>", "d359/<slug>"]` where `<rollup>` is the contact's domain (家 for family, else s897 — see whitelist above)
- **priority**: `"p3"`
- **dueString**: `"today"`

Batch up to 25 tasks per `add-tasks` call.

**Dedup**: Before creating tasks, search Todoist across every rollup domain in play (`@s897 | @家 | …`) for open tasks carrying the contact's `d359/<slug>` label. Skip any contact that already has an open reminder task.

### Step 4: Report

Output a table of results:

```
Stale contacts sweep — [date]

Scanned: N files with cadence + last_contact
Overdue: M contacts

| Name              | Cadence    | Last Contact | Days Overdue |
|-------------------|------------|--------------|--------------|
| Stuart Bowers     | monthly    | 2026-02-15   | 57           |
| ...               | ...        | ...          | ...          |

Created M Todoist tasks (label: s897, priority: p3, due: today)
Skipped K (already had open reminder)
```

If no contacts are overdue, say so and stop.

### Step 5: Update last_contact (optional, only if user confirms)

Do NOT auto-update `last_contact` fields. The user updates these manually after actual contact.

## Edge Cases

- Files with `cadence` but no `last_contact`: skip silently (contact not yet baselined)
- Files with `last_contact` but no `cadence`: skip silently
- `last_contact` in the future: skip (likely a data entry error, mention in report)
- Unknown cadence values: skip and warn

## Dependencies

- Todoist MCP server (add-tasks, find-tasks tools)
- Files: ~/vault/d359/*.md
