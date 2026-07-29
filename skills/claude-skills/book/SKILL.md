---
name: "book"
description: "Add a book to the hcmc reviews database by title — fetches author/year/pages from Open Library (fallback: Google Books) and creates the review stub with status: reading. Usage: /book <title> [--author X]"
user-invocable: true
---

# Add Book (/book)

Create a book entry in `~/vault/hcmc/reviews/<year>/` from just a title. The
script fetches metadata (author, first-publish year, pages, ISBN, subjects)
from Open Library, falling back to Google Books, and writes the same
frontmatter shape existing book reviews use (`media: book`,
`status: reading`, `draft: true`). Reviewing/scoring later is `/bookreview`.

## Execution

Run the script with the args verbatim and echo its output:

```bash
python3 ~/i446-monorepo/tools/hcmc/book-add.py <title words> [--author X] [--pick N]
```

- Output `+ <Title> — <Author> (year · pages) → hcmc/reviews/...` = created.
  `alt N:` lines list other candidates; if the user says the pick was wrong,
  re-run with `--pick N` after deleting the created file.
- `EXISTS: <path>` = the book is already in the database (any year) — report
  the path, do not create a duplicate.
- If the title is ambiguous and the user named an author in prose, pass it
  via `--author`.

## Response Style

Minimal. One line (plus alts if present). Do NOT explain. Do NOT ask for
confirmation.

## Notes

- The reviews index (`hcmc/reviews/reviews.md`) is generated elsewhere — do
  not hand-edit its counts.
- `/bookreview <title>` is the separate skill for writing the actual review
  when the book is finished.
