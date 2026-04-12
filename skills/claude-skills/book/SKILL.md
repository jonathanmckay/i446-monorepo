---
name: "book"
description: "Write a book review. Interactive: prompts for bullets, generates review in vault, opens Goodreads. Usage: /book <title>"
user-invocable: true
---

# Book Review (/book)

Interactive skill for writing a book review. The user provides the book title, then dictates bullet points, and the skill generates a polished review.

## Usage

```
/book <title>
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

### Step 5: Present for approval

Show the full review text. Ask:

```
Good? (y to save, or give feedback)
```

If the user gives feedback, revise and re-present. Loop until approved.

### Step 6: Save the review

Once approved:

1. **Determine the year folder.** Use today's date for the completion date unless the user specifies otherwise.

2. **Create the review file** at `~/vault/hcmc/reviews/{YEAR}/{kebab-case-title}.md`:

```markdown
---
title: "<Full Title>"
author: "<Author>"
date: YYYY-MM-DD
type: review
media: book
score: N
tags: [hcmc, review]
source: goodreads
series: "<Series Name>"
---

<review text>
```

- Omit `score` if skipped
- Omit `series` if not part of a series
- Omit `source` if user doesn't plan to post to Goodreads

3. **Open Goodreads** in Chrome so the user can paste the review:

```bash
open -a "Google Chrome" "https://www.goodreads.com/book/show/<search_query>"
```

Use a Goodreads search URL:
```bash
open -a "Google Chrome" "https://www.goodreads.com/search?q=$(python3 -c "import urllib.parse; print(urllib.parse.quote('TITLE AUTHOR'))")"
```

4. **Copy review text to clipboard** (without frontmatter) so the user can paste directly:

```bash
# Copy just the review body to clipboard
echo "<review text>" | pbcopy
```

### Step 7: Report

```
Saved: ~/vault/hcmc/reviews/YYYY/title.md (score: N)
Goodreads opened. Review copied to clipboard — paste away.
```
