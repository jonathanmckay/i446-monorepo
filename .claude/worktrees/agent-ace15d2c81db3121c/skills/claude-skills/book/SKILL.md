---
name: "book"
description: "Add, start, or finish a book in the hcmc reviews database. Bare title or '/book started <title>' fetches metadata from Open Library (fallback: Google Books) and creates a status: reading stub. '/book finished [<title>]' marks it done and bumps the weekly books-read count in Neon. Usage: /book <title> | /book started <title> | /book finished [<title>]"
user-invocable: true
---

# Add / Start / Finish Book (/book)

Two scripts back this skill, both operating on `~/vault/hcmc/reviews/<year>/`:

- `book-add.py` — looks up a title (Open Library, fallback Google Books) and
  creates a stub with `media: book`, `status: reading`, `draft: true`.
- `book-finish.py` — flips an existing `status: reading` stub to
  `status: finished` (+ a `finished:` date), leaving every other field
  (author, isbn, draft: true, ...) untouched so the file still works as a
  stub for a later `/bookreview`. Also increments the current week's books-read
  counter (`1分+1s!AM`) in the live Neon workbook via `ix-osa.sh`.

Reviewing/scoring the actual content later is the separate `/bookreview` skill.

## Routing

Look at the first word of the argument text:

- **First word is `started`** (case-insensitive): strip it, treat the rest as
  the title, run the **Add** path below exactly as if it were a bare title.
  (This is just an explicit synonym — `/book <title>` alone already does this.)
- **First word is `finished`**: strip it, treat the rest (if any) as an
  optional title, run the **Finish** path below.
- **Otherwise**: the whole argument text is a title — run the **Add** path.

## Add path (`/book <title>`, `/book started <title>`)

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

## Finish path (`/book finished [<title>]`)

```bash
python3 ~/i446-monorepo/tools/hcmc/book-finish.py [title words]
```

- No title: finishes the single `status: reading` book, if there's exactly
  one. Zero or multiple in-progress books → the script errors out naming
  them; ask the user to specify (or note there's nothing in progress).
- With a title: matches it against `status: reading` entries (case-insensitive
  substring on the frontmatter title). `NOT_READING:` means it's in the
  library but already finished or was never started reading.
- Output `✓ <Title> — finished → hcmc/reviews/... (Neon 1分+1s!AM +1: ...)`.
  If the Neon write fails (e.g. ix unreachable), the file is still updated;
  the script prints a `WARN:` line to stderr — surface that to the user
  rather than silently swallowing it.

## Response Style

Minimal. One line (plus alts/warnings if present). Do NOT explain. Do NOT ask
for confirmation.

## Notes

- The reviews index (`hcmc/reviews/reviews.md`) is generated elsewhere — do
  not hand-edit its counts.
- `/bookreview <title>` is the separate skill for writing the actual review;
  run it any time after `/book finished` on that title.
