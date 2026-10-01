---
name: "bookreview"
description: "Write a book review. Interactive: prompts for bullets, drafts in Obsidian for manual editing, publishes to the o315 blog on PUBLISH, then posts the review + stars to Goodreads via Chrome on PUSH. Usage: /bookreview <title>"
user-invocable: true
---

# Book Review (/bookreview)

Interactive skill for writing a book review. The user provides the book title, then dictates bullet points, and the skill generates a polished review.

## Usage

```
/bookreview <title>
```

## Flow

### Step 1: Identify the book

Take the `<title>` argument and search for the book to confirm:
- **Title** (full, including subtitle if relevant)
- **Author**
- **Series** (if applicable, e.g. "Foreigner, #1")

Use web search if needed to confirm author/series. Present a one-line confirmation:

```
Book: <Title> by <Author> [Series: <series>]
Ready for bullets. Type your thoughts — send DONE when finished.
```

### Step 2: Collect bullets

Enter an interactive loop. The user will send messages with bullet points, impressions, and raw thoughts about the book. Accumulate all bullets across multiple messages.

When the user sends `DONE` (or `done`, `d`, `finish`, `ok`, `go`), proceed to Step 3.

During collection, just acknowledge briefly: `Got it. Keep going or send DONE.`

### Step 3: Ask for score

Ask the user for a score (1-5) or skip:

```
Score? (1-5, or skip)
```

### Step 4: Generate the review

Using the collected bullets, write a review that:
- Is written in the user's voice (direct, opinionated, analytical)
- References the example reviews below for tone and structure
- Opens with a bold **Title Line** (a short, punchy summary phrase)
- Is 2-4 paragraphs, not a bullet list
- Does NOT summarize the plot — assumes the reader knows the book
- Focuses on what the book does well or poorly, and why it matters
- Draws connections to other works, ideas, or the user's experience when the bullets suggest them

**Tone reference** (from existing reviews):
- Analytical but personal
- States opinions as facts, then supports them
- Uses specific examples from the book
- Comfortable with ambiguity ("the book never quite delivers...")
- Often ends with a question or unresolved tension

### Step 5: Save draft and open for manual editing

Do **not** ask whether the composed review is good before saving. Save it as a draft immediately so the user can edit the real vault file.

Before saving, search `~/vault/hcmc/reviews/` for related reviews to link.

```bash
# Same author (case-insensitive grep on frontmatter)
grep -rl "^author:.*AUTHOR_NAME" ~/vault/hcmc/reviews/ --include="*.md"

# Same series (if applicable)
grep -rl "^series:.*SERIES_NAME" ~/vault/hcmc/reviews/ --include="*.md"
```

Build three link lists from the results (paths relative to the reviews/ root, e.g. `2025/the-blade-itself.md`):

- **`related:`** — all books by the same author (excluding the current review)
- **`series_next:` / `series_prev:`** — if this book is in a series and adjacent entries exist, link them. Also update the adjacent review's frontmatter to point back (add `series_next:` or `series_prev:` to the neighbor).
- **`series_number:`** — position in the series (if applicable)

Do NOT ask the user to confirm the links; just include them silently. If no related reviews exist, omit the fields.

Then:

1. **Determine the year folder.** Use today's date for the completion date unless the user specifies otherwise.

2. **Create the review file** at `~/vault/hcmc/reviews/{YEAR}/{kebab-case-title}.md`:

```markdown
---
title: "<Title Line>: <Full Book Title>"
slug: "<kebab-case-full-book-title>"
author: "<Author>"
date: YYYY-MM-DD
type: review
media: book
score: N
tags: [hcmc, review]
source: goodreads
series: "<Series Name>"
series_number: N
series_prev: "YYYY/prev-title.md"
series_next: "YYYY/next-title.md"
related:
  - "YYYY/other-by-author.md"
  - "YYYY/another-by-author.md"
---

**<Title Line>**

<review text>
```

- The frontmatter `title` is `<Title Line>: <Full Book Title>` — the punchy custom phrase first, then the actual book title after a colon. The same `<Title Line>` is repeated as the bold opener of the body. If the title line ends in a period, drop the period before the separator (`"Sharp Line: Book"`, not `"Sharp Line.: Book"`). If it ends in a question mark, the question mark wins and replaces the separator (`"Sharp Line? Book"`, not `"Sharp Line?: Book"`).
- The frontmatter `slug` is the kebab-case full book title, **not** the punchy title line. This keeps the blog URL stable at `/reviews/<book-title>/` even if the title line changes during manual editing.

- Omit `score` if skipped
- Omit `series`, `series_number`, `series_prev`, `series_next` if not part of a series
- Omit `related` if no other reviews by this author exist
- Omit `source` if user doesn't plan to post to Goodreads

3. **Open the saved review in Obsidian** so the user can edit the vault file manually:

```bash
python3 - <<'PY'
import urllib.parse, subprocess
path = "/Users/mckay/vault/hcmc/reviews/YYYY/title.md"
url = "obsidian://open?vault=vault&file=" + urllib.parse.quote(path.replace("/Users/mckay/vault/", "")) + "&newTab=true"
subprocess.run(["open", url], check=True)
PY
```

4. Stop and tell the user:

```
Draft saved and opened in Obsidian for manual editing.
When ready, send PUBLISH to deploy it to the o315 blog, then PUSH to post it to Goodreads.
```

### Step 6: Publish after manual edit

Only continue when the user explicitly sends `PUBLISH` (or `publish`). Then:

1. Re-read the saved review file from disk so any manual Obsidian edits are included.

2. **Goodreads is handled by PUSH** (Step 6b below), not here. Do not open
   Goodreads or copy to the clipboard during PUBLISH.

3. **Publish to the o315 blog** by syncing generated blog copies from the vault source of truth:

```bash
cd /Users/mckay/vault/hcmp/o315/blog && python3 scripts/sync-vault-reviews.py
```

4. **Commit, push, and verify the deploy:**

```bash
cd /Users/mckay/vault/hcmp/o315/blog
git add content/reviews/<slug>.md
git commit -m "Add review: <Title>"
git push
```

Then wait for the GitHub Actions deploy to complete:

```bash
# Get the latest run ID
gh run list --repo jonathanmckay/o315-blog-v3 --limit 1
# Watch it until it finishes
gh run watch <run_id> --repo jonathanmckay/o315-blog-v3
```

If the deploy fails, check `gh run view <run_id> --repo jonathanmckay/o315-blog-v3 --log-failed` and fix before continuing.

5. **Verify the review is live** by fetching the prod URL:

```bash
curl -s -o /dev/null -w "%{http_code}" "https://jonathanmckay.com/reviews/<slug>/"
```

If the response is `200`, open it in Chrome for the user to see:

```bash
open -a "Google Chrome" "https://jonathanmckay.com/reviews/<slug>/"
```

If not `200`, diagnose and fix. The review is not done until it loads in prod.

### Step 6b: PUSH — post the review to Goodreads

Trigger: the user sends `PUSH` (or `push`, `goodreads`) after PUBLISH. Also
accept `PUSH` on its own for a review file that is already published, and
`/bookreview push <title>` to run only this step on an existing review.
Posting is the user's explicit instruction; do not ask again.

Uses `mcp__claude-in-chrome__*` (one ToolSearch:
`select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__find,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__tabs_close_mcp`).
Goodreads is signed in on the **m5c7.com** Chrome profile; if two browsers
are connected, make sure the in-use one is that profile (navigate a tab to
`https://myaccount.google.com` and read the email; `select_browser` the other
if needed). If the extension isn't connected, `open -a "Google Chrome" https://claude.ai/chrome`,
wait ~6s, retry once; then report `WARN: Goodreads not posted — extension not connected` and stop.
Live-verified 2026-10-01 (Horus Rising).

1. **Prepare the text.** Re-read the review file from disk. Body = everything
   after the frontmatter. Convert to Goodreads markup: `**x**` → `<b>x</b>`,
   `*x*` → `<i>x</i>`, keep blank lines between paragraphs, strip trailing
   whitespace, drop any markdown links to `[text](url)` → `text`. Score =
   frontmatter `score` (1-5, same scale as Goodreads stars). `finished:` date
   if present.
2. **Find the book.** `tabs_context_mcp` (`createIfEmpty: true`) →
   `tabs_create_mcp` → `navigate` to `https://www.goodreads.com/search?q=<isbn>`
   (fallback `<title> <author>` URL-encoded). `wait` 2s. `find`
   `"search result link for the book titled <title>"`; take the `/book/show/<id>`
   from its href (re-run `find` once if it races the load).
3. **Open the editor**: `navigate` to `https://www.goodreads.com/review/edit/<id>`.
   `wait` 2s. If the page says *"you have also reviewed the following editions
   of this book"*, the user's shelf/rating lives on another edition: `find`
   `"link to the already-reviewed edition"`, click it (lands on that edition's
   book page), then `navigate` to `https://www.goodreads.com/review/edit/<that id>`.
   Keep everything on the edition that already carries the rating.
4. **Stars.** `find` `"current star rating value or selected star for this book"`.
   If it reports `Rating <score> out of 5`, leave it. Otherwise `find`
   `"Rate <score> out of 5 button"` and click it. Skip stars entirely if the
   review has no `score`.
5. **Text.** `find` `"review text area Write your review"` → click it. If it
   already contains text, `computer` `key` `cmd+a` first. Then `computer`
   `type` the prepared text (newlines are fine in this textarea).
6. **Date finished.** If `finished:` is today, `find` `"Set to today button next to Date finished (optional)"`
   and click it. Otherwise leave the dates alone.
7. **Post.** `find` `"Post your review submit button"` → click. `wait` 3s.
   The tab must now be at `https://www.goodreads.com/review/show/<review_id>`
   with title `<name>'s review of <title> | Goodreads`. If it isn't, one
   screenshot, report `WARN: Goodreads post not confirmed`, and leave the tab
   open for the user.
8. **Record the URL**: add `goodreads_review: "<review url>"` to the review
   file's frontmatter (after `source:`), so a later PUSH edits rather than
   duplicates (Goodreads's `/review/edit/<book id>` already loads the existing
   review for the same edition, so re-running PUSH updates in place).
9. `tabs_close_mcp` the tab.

### Step 7: Report

After PUBLISH:
```
Published: ~/vault/hcmc/reviews/YYYY/title.md (score: N)
Live at: https://jonathanmckay.com/reviews/<slug>/
Send PUSH to post it to Goodreads.
```

After PUSH:
```
Goodreads: <review url> (N stars, date finished set)
```
