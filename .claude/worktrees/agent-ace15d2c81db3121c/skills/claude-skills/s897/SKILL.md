---
name: "s897"
description: "Edit d359 people-database metadata from shorthand. met updates last_contact and deletes the robot outreach task; any frontmatter field can be set. Usage: /s897 Jessica Allen met yesterday"
user-invocable: true
---

# People Metadata (/s897)

Shorthand edits to the d359 people database (`~/vault/d359/`). One command,
one person, one metadata change.

## Primary path (use this)

```bash
python3 ~/i446-monorepo/tools/d359/s897_update.py "<full input>"
```

Pass the arguments through verbatim (add `--dry-run` first to preview). Echo
the script's one-line output. That's the whole skill — the script owns person
resolution, date parsing, frontmatter patching, and Todoist cleanup.

## What the script does

- **Person resolution:** longest prefix of the input matched against d359
  filenames (`jessica-allen-d359.md` and `Jordan Allen d359.md` styles both
  work), falling back to a unique-substring match on the first token. On
  ambiguity it exits 1 listing candidates — relay them and ask the user which
  one, then re-run with the fuller name.
- **`met [date]`** (date = `today` default, `yesterday`, `M/D`, `YYYY-MM-DD`):
  - sets `last_contact` (and `updated`) in the frontmatter
  - DELETES any open Todoist task labelled `d359/<slug>` whose content starts
    with 😈 — the robot outreach reminder is moot once contact happened.
    Deletion (not completion) is deliberate: no points are claimed for a task
    a daemon invented. Hand-written tasks (no 😈 prefix) are never touched.
- **`<field> <value>`** (e.g. `cadence monthly`, `role Chief of Staff`,
  `location Kirkland, WA`): sets that frontmatter field. Guarded by a known-
  field check (existing key in the file, or the script's COMMON_FIELDS list)
  so a typo'd verb can't silently mint a new key.

## Not this skill

- Meeting NOTES go through `/notes` (d359 body text, `YYYY.MM.DD` headers) —
  /s897 touches frontmatter only.
- Creating outreach tasks is `/stale-contacts`' job (its tasks carry the 😈
  automation marker + `d359/<slug>` label; /s897 is the other half that
  clears them when contact happens organically).

## Response Style

Terse. Run the script, echo its line. On ambiguity, list candidates and ask.

## Regression expectations

| Input | Expected |
|-------|----------|
| `/s897 Jessica Allen met yesterday` | `last_contact → <yesterday ISO>` + robot 😈 outreach task deleted |
| `/s897 jordan allen cadence monthly` | `cadence → monthly` (case-insensitive resolution) |
| `/s897 Allen met` | ambiguous → list both Allens, ask |
| `/s897 Jessica Allen flurb x` | unknown-field error, no write |
