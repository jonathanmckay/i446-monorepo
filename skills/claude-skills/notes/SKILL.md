---
name: "notes"
description: "Sort raw meeting notes from z_ibx/new-notes.md into their correct vault locations (d359 people docs, d358 meeting notes, o314 journal entries). Clears the inbox, then marks the notes habit done."
user-invocable: true
---

# Clean Notes

Sort the raw scratch-pad notes in `~/vault/z_ibx/new-notes.md` into their correct vault locations, then clear the inbox.

## When invoked with `/notes`

### Step 1: Read the inbox

Read `~/vault/z_ibx/new-notes.md`. If the file is empty (only the "DO NOT delete" header line), tell the user there's nothing to sort and stop.

### Step 2: Read the sort instructions

Read `~/vault/z_meta/new-notes-meta.md` for the full routing rules. Key summary:

- **`Name d359`** blocks → `h335/d359/Name d359.md` (1:1 people notes)
- **`Meeting Name d358`** blocks → `h335/d358/YYYY/Meeting Name d358.md` (general meeting notes)
- **`o314`** blocks → `hcmp/o314/YYYY/kebab-slug.md` (journal entries)
- **Unlabeled blocks** → attach to the nearest labeled block using context clues
- **Date headers** like `YYYY.MM.DD` at the start of a block set the date for all following blocks until the next date header

### Step 3: Parse blocks

Split the content into discrete blocks. A new block starts when you see:
- A line matching `Name d359` or `Name d358` (the tag is at the end of the line)
- A standalone line `o314` (journal entry marker)
- A date header followed by a name + tag on the next non-blank line

Each block has:
- **name**: the person or meeting name (for d358/d359), or a descriptive slug derived from the content (for o314)
- **type**: d359, d358, or o314
- **date**: from the nearest preceding date header (format: YYYY.MM.DD or YYYY-MM-DD). For o314 blocks with no date header, use today's date.
- **content**: the raw note text (everything between this header and the next block)

### Step 4: Route each block

Read the frontmatter templates from `h335/d358-d359-meta.md`.

**For d359 blocks:**
1. Search for an existing file: `find ~/vault/h335/d359 -maxdepth 2 -iname "*<name>*d359*"`
2. **If file exists**: Read it, then prepend a new `## YYYY-MM-DD` section below the About/profile section (if present) or below the frontmatter, above existing date entries. Update the `updated:` frontmatter field.
3. **If no file exists**: Create `h335/d359/<Name> d359.md` with:
   - Frontmatter: title, date, type: meeting-note, tags: [d359], context (detect from content — xk87 for school/kids, i9 for MSFT/GitHub, m5x2 for real estate, default i9), source: manual, status: active, updated
   - A `## YYYY-MM-DD` section with the raw content
4. **Update d359 index** (`h335/d359/d359.md`): Insert a row at the top of the table. If the person already has a row, remove the old one (so they appear once, at the top with the new date).

**For d358 blocks:**
1. Search for an existing file: `find ~/vault/h335/d358/YYYY -maxdepth 1 -iname "*<meeting name>*d358*"`
2. **If file exists**: Read it, then prepend a new `## YYYY-MM-DD` section below frontmatter, above existing entries. Update `updated:` field.
3. **If no file exists**: Create `h335/d358/YYYY/<Meeting Name> d358.md` with:
   - Frontmatter: title, date, type: meeting-note, tags: [d358, h335, <context>], source: manual, status: active, updated
   - A `## YYYY-MM-DD` section with the raw content
4. **Update d358 index** (`h335/d358/d358.md`): Insert a row at the top of the Recent Meetings table. Remove duplicate if updating existing meeting.

**For o314 blocks:**
1. Derive a kebab-case slug from the content (2–5 words capturing the main theme, e.g. `ai-cli-vs-agent-mode`, `on-legacy-and-identity`)
2. Create `hcmp/o314/YYYY/kebab-slug.md` with:
   ```yaml
   ---
   title: "Descriptive title (same theme as slug, title-cased)"
   date: YYYY-MM-DD
   type: journal
   tags: [o314]
   source: manual
   ---
   ```
   Followed by the raw content as-is (no body header needed — the file is the entry).
3. **Update the year index** (`hcmp/o314/YYYY/YYYY.md`):
   - Increment the month count in the entry list (e.g., `- March (7)` → `- March (8)`)
   - Increment the total entry count in the `**N entries**` line at the top
   - If the year folder or year index doesn't exist, create them following the format in `hcmp/o314/2026/2026.md`
4. **Update the main o314 index** (`hcmp/o314/o314.md`): Increment the total entry count in the `**N entries**` line.

### Step 5: Clear the inbox

Overwrite `~/vault/z_ibx/new-notes.md` with only the header line:
```
note: DO NOT delete this doc when you clean and categorize everything below this heading.

```

### Step 6: Report

Tell the user what was sorted:
```
Sorted N blocks from new-notes:

o314 (journal):
- ai-cli-vs-agent-mode → created (2026-03-30)

d359 (people):
- 彭老师 → updated (2026-03-26)
- Johanna → created (2026-03-26)

d358 (meetings):
- Experimentation weekly → created (2026-03-26)

Inbox cleared.
```

### Step 7: Extract action items

After sorting, scan the content that was just routed for action items. This step surfaces implicit and explicit tasks so nothing falls through the cracks.

**7a. Identify action items from the sorted note content.**

Look for lines or phrases matching these patterns:
- Explicit markers: "Action:", "TODO:", "Follow up on...", "Next step:"
- Commitments: "I need to...", "I should...", "I'll send...", "I'll check...", "I'll schedule...", "Will do..."
- Collaborative: "Let's...", "We should...", "We need to..."
- Requests: "Ask [person] about...", "Check with...", "Get [thing] from...", "Ping [person]..."
- Deadlines or urgency cues: "by Friday", "before the meeting", "ASAP", "this week"

If no action items are found across all sorted blocks, skip silently to Step 8.

**7b. For each extracted action item, infer:**

1. **Task content** — clean, concise phrasing. Strip filler words; keep it actionable. Example: "I should probably check with Andy about the deploy timeline" → "Check with Andy about deploy timeline"
2. **Project label** — derive from the block's context tag (the same context detected in Step 4):
   - i9 (Microsoft/GitHub), m5x2 (real estate), xk87 (school/kids), hcm (mindfulness), hcb (health), etc.
   - If the block has a d359 person file with a `context:` frontmatter field, use that context as the project label
   - If ambiguous, default to the block's context tag; if still unclear, omit the label
3. **Due date** — default to tomorrow. Override if urgency is implied:
   - "ASAP" / "today" → today
   - "by Friday" → that Friday
   - "this week" → Friday of current week
   - "next week" → Monday of next week

**7c. Present the action items to the user:**

```
Action items extracted:

1. Check with Andy about deploy timeline [@i9, due tomorrow]
2. Send lease renewal to tenant [@m5x2, due tomorrow]
3. Schedule dentist appointment [@hcb, due Friday]

Create these tasks? (y/n/edit)
```

- **y** — create all tasks via the Todoist MCP `add-tasks` tool using the inferred content, project label, and due date
- **n** — skip task creation entirely
- **edit** — let the user modify the list (add, remove, change labels/dates), then confirm and create

**7d. Create confirmed tasks.**

Use the Todoist MCP `add-tasks` tool. For each task:
- `content`: the cleaned task text
- `dueString`: the inferred due date in natural language (e.g., "tomorrow", "Friday")
- Assign to the appropriate Todoist project using the label/domain code

### Step 8: Archive stale z_ibx files

Move any non-essential files in `~/vault/z_ibx/` that are older than 14 days to `~/vault/z_ibx/archive/YYYY-MM/`. Preserve these active state files (never archive them):
- `new-notes.md`
- `completed-today.json`
- `task-queue.json`
- `mtg-briefs.json`
- `mtg-postbriefs.json`
- `.syncthing-test`
- Directories (`outlook-backfill/`, `overnight/`, `archive/`)

```bash
mkdir -p ~/vault/z_ibx/archive/$(date +%Y-%m)
find ~/vault/z_ibx -maxdepth 1 -type f -mtime +14 \
  ! -name "new-notes.md" ! -name "completed-today.json" \
  ! -name "task-queue.json" ! -name "mtg-briefs.json" \
  ! -name "mtg-postbriefs.json" ! -name ".syncthing-test" \
  -exec mv {} ~/vault/z_ibx/archive/$(date +%Y-%m)/ \;
```

Report archived files if any: `Archived N stale files from z_ibx.`

### Step 9: Mark notes habit done

After action item handling (or skipping), execute the `/did` skill for habit `notes` — follow the full `/did` flow exactly as if the user had typed `/did notes`. This writes 1 to the `notes` column in today's 0₦ row and closes any matching 0neon Todoist task.

## Rules

- **Raw notes only** — never summarize, rewrite, or edit the note content. Copy as-is.
- **Profile section stays pinned** — when prepending to d359 files, insert below the About section, never above it.
- **Reverse chronological** — newest entries go at the top (below profile/frontmatter).
- **Detect context from content** — school/kid mentions → xk87, Microsoft/GitHub → i9, real estate/property → m5x2.
- **If a block is ambiguous** (no clear d358/d359/o314 tag), ask the user before routing it.
