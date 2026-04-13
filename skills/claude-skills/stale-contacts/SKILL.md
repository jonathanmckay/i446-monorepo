---
name: "stale-contacts"
description: "Scan d359 contacts for overdue outreach based on cadence. Creates Todoist tasks. Usage: /stale-contacts"
user-invocable: true
---

# Stale Contacts Sweep (/stale-contacts)

Scan ~/vault/d359/ for contacts with overdue outreach cadence, then create Todoist reminder tasks.

## Response Style

Terse. No preamble. Run the sweep, report results.

## Steps

### Step 0: Get current date

Run `date +%Y-%m-%d` to get today's date. Do not rely on context variables.

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
- **labels**: `["s897"]`
- **priority**: `"p3"`
- **dueString**: `"today"`

Batch up to 25 tasks per `add-tasks` call.

**Dedup**: Before creating tasks, search Todoist for existing open tasks containing "Reach out to [Name]" with label `s897`. Skip any contact that already has an open reminder task.

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
