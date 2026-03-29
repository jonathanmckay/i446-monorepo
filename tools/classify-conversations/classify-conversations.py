#!/usr/bin/env python3
"""
Conversation Auto-Classifier

Scans Claude Code session transcripts, classifies them by domain,
generates summary markdown files, and places them in the correct
vault folder.

Usage:
    python classify-conversations.py              # Process new sessions
    python classify-conversations.py --reprocess  # Reprocess all sessions
    python classify-conversations.py --dry-run    # Preview without writing
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

VAULT_DIR = Path.home() / "vault"
TRANSCRIPTS_DIR = Path.home() / ".claude" / "projects" / "-Users-mckay"
PROCESSED_FILE = VAULT_DIR / ".processed-sessions"

# Domain mapping — classifier output → vault folder
DOMAINS = {
    "g245": "g245",
    "h335": "h335",
    "i9": "h335/i9",
    "m5x2": "h335/m5x2",
    "m828": "h335/m828",
    "hcmc": "hcmc",
    "hcmp": "hcmp",
    "hcbi": "hcbi",
    "xk88": "xk88",
    "xk87": "xk87",
    "s897": "s897",
    "qz12": "qz12",
    "i447": "i447",
    "general": "ibx",
}

DOMAIN_DESCRIPTIONS = """
g245 = Goal setting, tracking, habits, rituals, the life system itself
h335 = Work and career (general)
i9 = Growth @ Microsoft specifically
m5x2 = McKay Capital, entrepreneurism, real estate, investments
m828 = Non-profit activities
hcmc = Media, reading, learning, language, books, articles
hcmp = Mindfulness, spiritual practice, philosophy
hcbi = Health, fitness, nutrition, body
xk88 = Marriage, dating, partnership
xk87 = Family, siblings, kids
s897 = Friends, social life
qz12 = Finance, capital, money, budgeting
i447 = Infrastructure, housing, logistics, moving, sleep
general = Doesn't fit any specific domain
""".strip()


def load_processed():
    """Load set of already-processed session IDs."""
    if PROCESSED_FILE.exists():
        return set(PROCESSED_FILE.read_text().strip().split("\n"))
    return set()


def save_processed(processed: set):
    """Save set of processed session IDs."""
    PROCESSED_FILE.write_text("\n".join(sorted(processed)) + "\n")


def extract_conversation(filepath: Path) -> dict:
    """Extract user and assistant messages from a JSONL transcript."""
    messages = []
    session_id = filepath.stem
    first_timestamp = None
    last_timestamp = None

    with open(filepath) as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = obj.get("type")
            timestamp = obj.get("timestamp")

            if timestamp:
                if first_timestamp is None:
                    first_timestamp = timestamp
                last_timestamp = timestamp

            if msg_type == "user":
                content = obj.get("message", {}).get("content", "")
                if isinstance(content, str) and content.strip():
                    messages.append({"role": "user", "text": content[:500]})
                elif isinstance(content, list):
                    # Handle tool results and multi-part content
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    if text_parts:
                        messages.append({"role": "user", "text": " ".join(text_parts)[:500]})

            elif msg_type == "assistant":
                content = obj.get("message", {}).get("content", [])
                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                    if text_parts:
                        messages.append({"role": "assistant", "text": " ".join(text_parts)[:500]})
                elif isinstance(content, str):
                    messages.append({"role": "assistant", "text": content[:500]})

    return {
        "session_id": session_id,
        "messages": messages,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
    }


def build_summary_prompt(conversation: dict) -> str:
    """Build a prompt for the LLM to classify and summarize."""
    # Take a sample of messages (first 10 + last 10 to capture scope)
    msgs = conversation["messages"]
    if len(msgs) > 20:
        sample = msgs[:10] + msgs[-10:]
    else:
        sample = msgs

    transcript = ""
    for msg in sample:
        role = msg["role"].upper()
        transcript += f"\n{role}: {msg['text']}\n"

    return f"""Classify and summarize this Claude Code conversation.

## Domain codes:
{DOMAIN_DESCRIPTIONS}

## Conversation transcript (sampled):
{transcript}

## Instructions:
1. Pick the single best domain code from the list above.
2. If the conversation spans multiple domains, pick the primary one.
3. Write a concise title (5-10 words).
4. Write 3-5 bullet point summary of key topics, decisions, or outcomes.
5. List any tags (lowercase, no spaces).

Respond in EXACTLY this format:
DOMAIN: <code>
TITLE: <title>
SUMMARY:
- <bullet 1>
- <bullet 2>
- <bullet 3>
TAGS: <tag1>, <tag2>, <tag3>"""


def classify_with_api(prompt: str) -> dict:
    """Call Anthropic API to classify and summarize."""
    import urllib.request

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Try .env file in vault directory
        env_path = VAULT_DIR / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break

    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set")
        print("Set it via: export ANTHROPIC_API_KEY=sk-ant-...")
        print("Or create ~/vault/.env with: ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode())

    text = result["content"][0]["text"]

    # Parse structured response
    domain = "general"
    title = "Untitled Conversation"
    summary_lines = []
    tags = []

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("DOMAIN:"):
            domain = line.split(":", 1)[1].strip().lower()
        elif line.startswith("TITLE:"):
            title = line.split(":", 1)[1].strip()
        elif line.startswith("- "):
            summary_lines.append(line)
        elif line.startswith("TAGS:"):
            tags = [t.strip() for t in line.split(":", 1)[1].split(",")]

    return {
        "domain": domain if domain in DOMAINS else "general",
        "title": title,
        "summary": "\n".join(summary_lines),
        "tags": tags,
    }


def write_summary(conversation: dict, classification: dict, dry_run: bool = False):
    """Write a summary markdown file to the vault."""
    domain = classification["domain"]
    folder = VAULT_DIR / DOMAINS.get(domain, "ibx")
    folder.mkdir(parents=True, exist_ok=True)

    # Parse date from timestamp
    ts = conversation.get("first_timestamp", "")
    try:
        date = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        date = datetime.now().strftime("%Y-%m-%d")

    # Generate filename
    slug = re.sub(r"[^a-z0-9]+", "-", classification["title"].lower()).strip("-")[:60]
    filename = f"{date}-{slug}.md"
    filepath = folder / filename

    content = f"""---
title: "{classification['title']}"
date: {date}
type: conversation
tags: [{', '.join(classification['tags'])}]
source: claude
session_id: {conversation['session_id']}
---

# {classification['title']}

{classification['summary']}
"""

    if dry_run:
        print(f"\n  Would write: {filepath}")
        print(f"  Domain: {domain}")
        print(f"  Title: {classification['title']}")
        print(f"  Tags: {classification['tags']}")
    else:
        filepath.write_text(content)
        print(f"  Wrote: {filepath}")

    return filepath


def main():
    parser = argparse.ArgumentParser(description="Classify Claude Code conversations")
    parser.add_argument("--reprocess", action="store_true", help="Reprocess all sessions")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    processed = set() if args.reprocess else load_processed()
    transcripts = sorted(TRANSCRIPTS_DIR.glob("*.jsonl"))

    if not transcripts:
        print("No session transcripts found.")
        return

    new_count = 0
    for filepath in transcripts:
        session_id = filepath.stem
        if session_id in processed:
            continue

        print(f"\nProcessing: {session_id}")
        conversation = extract_conversation(filepath)

        if len(conversation["messages"]) < 3:
            print("  Skipping (too short)")
            processed.add(session_id)
            continue

        prompt = build_summary_prompt(conversation)
        classification = classify_with_api(prompt)
        write_summary(conversation, classification, dry_run=args.dry_run)

        processed.add(session_id)
        new_count += 1

    if not args.dry_run:
        save_processed(processed)

    print(f"\nDone. Processed {new_count} new session(s).")


if __name__ == "__main__":
    main()
