---
name: clean-notes
description: Sort raw meeting notes from z_ibx/new-notes.md into their correct vault locations (d359 people docs, d358 meeting notes, o314 journal entries). Clears the inbox when done.
user-invocable: true
---

# Clean Notes

Sort the raw scratch-pad notes in `~/vault/z_ibx/new-notes.md` into their correct vault locations, then clear the inbox.

## When invoked with `/clean-notes`

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

## Rules

- **Raw notes only** — never summarize, rewrite, or edit the note content. Copy as-is.
- **Profile section stays pinned** — when prepending to d359 files, insert below the About section, never above it.
- **Reverse chronological** — newest entries go at the top (below profile/frontmatter).
- **Detect context from content** — school/kid mentions → xk87, Microsoft/GitHub → i9, real estate/property → m5x2.
- **If a block is ambiguous** (no clear d358/d359/o314 tag), ask the user before routing it.
