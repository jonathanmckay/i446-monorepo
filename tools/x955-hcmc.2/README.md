# Inkwell

Multimedia reading list collator with API access for AI traversal.

## What

A system to collect and organize links, emails, articles, and other media into a single reading list — accessible both by humans and AI agents.

## Stack

- **Readwise Reader** — ingestion layer (articles, emails, PDFs, web pages)
- **Readwise API** — programmatic access for AI traversal
- **Obsidian** (planned) — local Markdown knowledge base via Readwise sync plugin

## Current State (2026-02-13)

- Readwise account active, API token configured
- `save_link.py` script in x954 workspace can save URLs to Readwise Reader inbox
- 1 item saved: Gmail email link

## Access

- **UI:** reader.readwise.io
- **API:** `https://readwise.io/api/v3/` with Bearer/Token auth
- **Token:** stored as `READWISE_TOKEN` env var

## Next Steps

- [ ] Bulk import workflow (batch URLs, email forwarding)
- [ ] Readwise → Obsidian vault sync
- [ ] AI agent that can query/summarize the reading list
- [ ] Tagging/categorization system
