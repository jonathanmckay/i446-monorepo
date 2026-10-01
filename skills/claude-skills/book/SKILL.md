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
`select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__find,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__tabs_close_mcp`).
Best-effort: the vault + Neon work above is already done and must never be
rolled back because Goodreads failed. Live-verified 2026-10-01 against the
current Goodreads UI; the `find` queries below are natural-language, not CSS.

**Which Chrome:** Goodreads is signed in on the **m5c7.com** Chrome profile,
and the Claude extension is installed only in the m5c7.com and MSFT profiles.
If `list_connected_browsers` shows two browsers and the in-use one is not
m5c7.com (check by navigating a tab to `https://myaccount.google.com` and
reading the email), `select_browser` the other one.

1. **Identifiers** from the stub the script just updated (path is in its
   `→ hcmc/reviews/...` output): frontmatter `title`, `author`, `isbn`.
   Search URL = `https://www.goodreads.com/search?q=<isbn>` when `isbn` is
   present, else `https://www.goodreads.com/search?q=<title> <author>`
   URL-encoded.
2. **Connect**: `tabs_context_mcp` (`createIfEmpty: true`). If it reports the
   extension isn't connected while Chrome is running, run
   `open -a "Google Chrome" https://claude.ai/chrome`, wait ~6s, retry ONCE.
   Still down → `open -a "Google Chrome" "<search url>"` and report
   `WARN: Goodreads not updated — Chrome extension not connected; page opened for manual shelving`.
   Then `tabs_create_mcp` and `navigate` the new tab to the search URL.
3. **Land on the book page.** An ISBN search returns a results list (it does
   not redirect). `find` `"search result link for the book titled <title>"`,
   click the first match (href `/book/show/...`), `computer` `wait` 3s. If
   `find` returns nothing (re-run once; the first call can race the page
   load), report `WARN: Goodreads not updated — no search match for <title>`
   and stop.
4. **Confirm the book**: `find` `"book title heading"` → must match the stub
   title (subtitle/series suffix differences are fine; a different author is
   not).
5. **Sign-in wall**: if `find` `"sign in button"` returns a prominent
   Sign in / Sign in with control and no shelf button exists, stop with
   `WARN: Goodreads not updated — not signed in`. Never enter credentials.
6. **Read the current shelf**: `find`
   `"shelf button showing Want to Read, Currently Reading, or Read on the book page"`.
   The button's accessible name is one of:
   - `Shelved as 'Read'. Tap to edit shelf for this book` → already done, skip to step 9.
   - `Shelved as '<other>'. Tap to edit shelf for this book` → click this button; it opens the chooser.
   - plain `Want to Read` (unshelved book) → do NOT click it (that shelves as Want to Read). `find`
     `"Tap to edit shelf dropdown chevron next to the Want to Read button"` and click that instead.
7. **Choose Read.** The dialog is titled `Step 1 of 2: Choose a shelf for this
   book` with buttons `Want to Read`, `Currently Reading`, `Read` (plus
   custom shelves, `Remove from my shelf`, `Continue to tags`, `Close`).
   `find` `"Read shelf button in the choose-a-shelf dialog"` and click the one
   whose name is exactly `Read` (or `Read, selected`), never `Want to Read`
   or `Did Not Finish`. Wait 2s.
8. **Dismiss follow-ups.** Goodreads may show Step 2 (tags) and/or a
   "What did you think?" rating/review prompt. `find` `"Close button on the
   dialog"` and click it. Do NOT set stars, tags, or review text — that's
   `/bookreview`.
9. **Verify**: `find` the shelf button again; its name must now begin
   `Shelved as 'Read'`. If not, one `computer` screenshot, report
   `WARN: Goodreads shelf still shows <name>`, stop.
10. **Close the tab** you created (`tabs_close_mcp`).
11. Append to the one-line response: `· Goodreads: Read ✓` (or
    `· Goodreads: already Read`), else the `WARN:` line.

If a `find` query returns nothing because the Goodreads layout changed, take
one screenshot, locate the control visually, and update the query text here
before finishing.

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
