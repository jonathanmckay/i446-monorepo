---
name: "m5x2share"
description: "Share a vault markdown doc into the m5x2 Google Drive as a Google Doc. Lists the m5x2 Main folders and asks which one to share into, then imports and links back. Usage: /m5x2share <doc> [folder hint]"
user-invocable: true
---

# m5x2 Share (/m5x2share)

Put a shareable Google Doc copy of a vault doc into the m5x2 shared drive so
Ian/Louisa/team can read it. Vault stays source of truth (no flip mode).
Google-side identity is always **mckay@m5c7.com** (the m5x2.com OAuth does not
work — see memory; the m5c7 account has edit rights on the m5x2 drives).

## Steps

### 1. Resolve the doc

Like `/msftshare`: vault-relative path or unambiguous filename, resolved with
`mdfind -onlyin ~/vault` (never `find ~`). **Never guess** — if multiple files
match, print the candidates and stop. Prefer a doc already open in context
when the user says "this doc".

### 2. Pick the destination folder — ASK, don't assume

Skip the ask only when the user named a folder in the args AND it matches
exactly one folder, or when re-sharing a doc whose frontmatter already carries
`m5x2_folder_id` (reuse it silently).

Otherwise list the top-level folders of the **m5x2 Main** shared drive
(id `0ALnip0aznECYUk9PVA`):

```
mcp__workspace-mcp__list_drive_items
  user_google_email: mckay@m5c7.com
  drive_id: 0ALnip0aznECYUk9PVA
  folder_id: 0ALnip0aznECYUk9PVA   # drive id = its root
  file_type: folder
```

Present the folders with AskUserQuestion (use multiple questions/pages only if
needed; the user can always pick "Other" and name a subfolder or a different
m5x2 drive). Other m5x2 shared drives, for the "Other" path:
`m5x2 (v2)` 0AB9fKpxp0FjjUk9PVA · `m5x2 Fundraising & Legal` 0AFaxxyq5CgQ0Uk9PVA ·
`m5x2 HR` 0AItivs5bK2F9Uk9PVA · `m5x2 Investor - K1s` 0AKy3ceSYQ_fjUk9PVA ·
`m5x2 Large Files` 0AGF6XDD__iUjUk9PVA. If they name a subfolder, list the
chosen folder again to find its id.

### 3. Import as a Google Doc

```
mcp__workspace-mcp__import_to_google_doc
  user_google_email: mckay@m5c7.com
  file_name: <doc title from frontmatter, else the filename without .md>
  file_path: <absolute vault path>
  folder_id: <chosen folder id>
  source_format: md
```

Drive converts markdown natively (headings/lists/bold survive; `[[wikilinks]]`
and `![[embeds]]` do not — warn if the source contains them, same rule as
msftshare).

### 4. Link back into the vault doc

- `mcp__workspace-mcp__get_drive_shareable_link` on the new doc id.
- In the vault doc's frontmatter set: `m5x2_doc_id`, `m5x2_folder_id`,
  `m5x2_share_url`, and update `updated:`.
- Inject a clickable link line at the top of the body, marked with the
  sentinel `<!-- m5x2share:gdoc-link -->` so re-runs replace it in place:
  ```markdown
  <!-- m5x2share:gdoc-link -->
  > 📄 Shared to m5x2 Drive: [Google Doc](<share url>)
  ```

### 5. Report

One line: doc name → drive/folder name, plus the share URL.

## Re-runs / refresh

Import cannot update an existing Doc in place, so re-running a doc that
already has `m5x2_doc_id` creates a FRESH import into the same folder and
updates the frontmatter ids/url. Tell the user the share link CHANGED (the
old Doc and its link are left behind — offer to trash it in the Drive UI).
If the old link must survive (already circulated), stop and say so instead
of silently rotating it.

## Safety

- Resolution never guesses (13k+ vault docs, duplicate basenames).
- Never share a doc you haven't read this session — check for obviously
  personal content leaking into a team drive (vault codes in the title are
  fine; the `file_name` should be the human title).
- 分/points notation stays as-is; never convert to $.
