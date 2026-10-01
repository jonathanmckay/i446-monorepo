---
name: "book"
description: "Add, start, or finish a book in the hcmc reviews database. Bare title or '/book started <title>' fetches metadata from Open Library (fallback: Google Books) and creates a status: reading stub. '/book finished [<title>]' marks it done, bumps the weekly books-read count in Neon, and shelves it as Read on Goodreads via Chrome. Usage: /book <title> | /book started <title> | /book finished [<title>]"
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
  counter (`1分+1s!AM`) in the live Neon workbook via `ix-osa.sh`. The skill
  then shelves the book as **Read** on Goodreads through the Chrome extension
  (see "Goodreads step" below).

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

### Goodreads step (after a successful finish)

Shelve the book as **Read** on the user's Goodreads account using the
`mcp__claude-in-chrome__*` tools (load them with ONE ToolSearch:
`select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__find,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__tabs_close_mcp`).
This step is best-effort: the vault + Neon work above is already done and
must never be rolled back because Goodreads failed.

1. **Get identifiers** from the stub the script just updated (the path is in
   its `→ hcmc/reviews/...` output): frontmatter `title`, `author`, `isbn`.
   Search URL = `https://www.goodreads.com/search?q=<isbn>` when `isbn` is
   present (exact match), else `https://www.goodreads.com/search?q=<title> <author>`
   URL-encoded.
2. **Open it**: `tabs_context_mcp` (`createIfEmpty: true`) → `tabs_create_mcp`
   → `navigate` to the search URL. If the extension is not connected or
   doesn't respond, retry ONCE; then fall back to
   `open -a "Google Chrome" "<search url>"` and report
   `WARN: Goodreads not updated — Chrome extension not connected; page opened for manual shelving`.
   Do not keep retrying.
3. **Land on the book page.** An ISBN search usually redirects straight to
   `/book/show/...`. If the URL still contains `/search`, `find`
   `"search result link for the book titled <title>"` and click the first
   match whose text matches the title (and author, if shown). If nothing
   matches, report `WARN: Goodreads not updated — no search match for <title>`
   and stop.
4. **Confirm it's the right book**: `find` `"book title heading"` and check it
   matches the stub title (subtitle differences are fine; a different author
   is not).
5. **Check for a sign-in wall.** If `find` `"sign in button"` returns a
   prominent sign-in / "Sign in with" control instead of the shelf button, stop
   and report `WARN: Goodreads not updated — not signed in`. Never enter
   credentials.
6. **Read the current shelf.** `find` `"shelf button showing Want to Read, Currently Reading, or Read"`.
   - Text already `Read` → nothing to do; go to step 8.
   - Otherwise `find` `"dropdown arrow next to the shelf button"` and click
     it, then `find` `"menu option Read"` (the plain `Read` item, not
     `Want to Read`) and click it.
7. **Dismiss the review modal.** Goodreads usually opens a "What did you
   think?" rating/review dialog after shelving as Read. `find`
   `"close button on the review dialog"` (or a `Done` button) and click it.
   Do NOT set a star rating or write anything here — that's `/bookreview`.
8. **Verify**: `find` the shelf button again; its text must now be `Read`.
   If it isn't, take one `computer` screenshot, report
   `WARN: Goodreads shelf still shows <text>`, and stop.
9. **Close the tab** you created (`tabs_close_mcp`).
10. Append to the one-line response: `· Goodreads: Read ✓` on success, or the
    `WARN:` line otherwise.

Goodreads UI drift: the selectors above are natural-language `find` queries,
not CSS. If the page layout changes and a query returns nothing, take a
screenshot, locate the control visually, and update the query text in this
file before finishing.

## Response Style

Minimal. One line (plus alts/warnings if present). Do NOT explain. Do NOT ask
for confirmation.

## Notes

- The reviews index (`hcmc/reviews/reviews.md`) is generated elsewhere — do
  not hand-edit its counts.
- `/bookreview <title>` is the separate skill for writing the actual review;
  run it any time after `/book finished` on that title. Rating and review
  text on Goodreads belong there, not in the shelve-as-Read step.
- The Goodreads step requires the Claude in Chrome extension connected and
  the user already signed in to goodreads.com in that Chrome profile.
