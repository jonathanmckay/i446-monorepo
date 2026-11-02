---
name: "prep"
description: "Generate a pre-meeting brief for a 1:1. Pulls d359 notes, open tasks, and calendar context. Usage: /prep <person name>"
user-invocable: true
---

# 1:1 Prep Brief (/prep)

Generate a concise pre-meeting brief for an upcoming 1:1.

## Usage

```
/prep <person name>
```

## Response Style

**Compact.** The entire brief should fit on one screen. No preamble, no explanation of what you're doing. Execute the steps silently, then output only the final brief.

## Steps

### Step 1: Resolve the person

Search `~/vault/d359/` for a file matching `<person name>` (case-insensitive, partial OK — e.g. "luke" matches "Luke Hoban d359.md"). Use the same scored matching as `/send`:

- Score 3: exact name match (minus " d359" suffix)
- Score 2: full name found in filename
- Score 1: slug/partial match

If multiple matches, pick the highest score. If ambiguous (multiple score-3 or score-2 hits), list them and ask the user to clarify.

Read the matched d359 file fully.

### Step 2: Extract profile and recent notes

From the d359 file:

1. **Profile section** — the top of the file (before any date headers). Extract role, team, key context.
2. **Recent meeting notes** — find the last 2–3 `YYYY.MM.DD` date-headed sections. Summarize key discussion points from each (one bullet per meeting, max).
3. **Last met date** — the most recent date header.

### Step 3: Find open tasks

Use the `find-tasks` Todoist MCP tool to search for active tasks mentioning this person's name:

```
mcp__todoist__find-tasks with searchText: "<person name>"
```

Also search by any labels that match their known team/domain (e.g. `i9` for Microsoft colleagues, `m5x2` for McKay Capital). Collect up to 5 relevant open tasks.

### Step 4: Check calendar

Use `mcp__google-calendar-mcp__search-events` to find upcoming meetings with this person (search by name). Look at the next 7 days. Note any scheduled meetings, their times, and agendas if present.

### Step 5: Generate the brief

Output the following structure — nothing else:

```
## 1:1 Prep: [Person Name]
**Role:** [from d359 profile]
**Last met:** [most recent date header from d359]
**Next meeting:** [from calendar, or "none scheduled"]

### Previous Discussion
- [YYYY.MM.DD] [key point from that meeting]
- [YYYY.MM.DD] [key point]
- [YYYY.MM.DD] [key point]

### Open Items
- [task 1 from Todoist]
- [task 2]
(or "None" if no matching tasks)

### Suggested Agenda
1. Follow up on [specific thing from most recent meeting]
2. [open task that needs discussion]
3. [calendar/timing context if relevant]
```

Keep each bullet to one line. The suggested agenda should be 2–4 items, synthesized from the previous discussion and open items — not a mechanical repetition of them.
