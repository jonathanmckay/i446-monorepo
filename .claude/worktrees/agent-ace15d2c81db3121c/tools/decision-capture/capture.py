#!/usr/bin/env python3
"""
Scan the last 24h of Claude Code conversation logs for meaningful decisions.
Append them to ~/vault/g245/decision-log.md.
Runs as a local cron job (10pm PT daily).
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import anthropic

LOGS_DIR = Path.home() / ".claude/projects"
DECISION_LOG = Path.home() / "vault/g245/decision-log.md"
CUTOFF_HOURS = 24

DOMAIN_CODES = [
    "i9", "m5x2", "xk87", "xk88", "g245", "s897", "hcmc", "hcm",
    "hcb", "hcbi", "hcbp", "i447", "i446", "qz12", "n156", "epcn",
    "xk23", "h335", "d359", "infra",
]

SYSTEM_PROMPT = """You extract important decisions from Claude Code conversation logs.

A "decision" is when the user chose between options or committed to an approach:
- Architecture choices (e.g., "use Playwright instead of CUA for AppFolio signing")
- Tool/library selections (e.g., "persistent browser context for 2FA")
- Strategy decisions (e.g., "keep m5x2 vault minimal, use Google Docs instead")
- Process changes (e.g., "log automations to Google Sheets not SQLite")
- Design decisions (e.g., "inline todos in source files, not separate TODO.md")

Skip trivial decisions: formatting, typos, variable names, import ordering.

Domain codes: i9 (Microsoft/GitHub/work), m5x2 (McKay Capital/real estate), xk87 (kids/family),
xk88 (relationships), g245 (goals/planning), s897 (social), hcmc (media), hcm (mindfulness),
hcb/hcbi/hcbp (health), i447/i446 (infrastructure/software), qz12 (finance), d359 (people/CRM).

For each decision found, output JSON array:
[
  {
    "title": "brief title",
    "domain": "domain_code",
    "decision": "what was decided",
    "why": "the reasoning",
    "alternatives": "what else was considered"
  }
]

If no meaningful decisions, output: []
"""


def find_recent_logs() -> list[Path]:
    """Find conversation log files modified in the last CUTOFF_HOURS."""
    cutoff = time.time() - (CUTOFF_HOURS * 3600)
    logs = []
    for jsonl in LOGS_DIR.rglob("*.jsonl"):
        if jsonl.stat().st_mtime >= cutoff:
            logs.append(jsonl)
    return sorted(logs, key=lambda p: p.stat().st_mtime, reverse=True)


def extract_conversation_text(log_path: Path, max_chars: int = 80_000) -> str:
    """Extract user and assistant messages from a JSONL conversation log."""
    lines = []
    total = 0
    for line in log_path.read_text(errors="replace").splitlines():
        if total > max_chars:
            break
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg_type = entry.get("type", "")
        if msg_type not in ("user", "assistant"):
            continue
        content = entry.get("message", {}).get("content", "")
        if isinstance(content, list):
            # Extract text blocks only
            content = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if not content or len(content) < 20:
            continue
        role = "USER" if msg_type == "user" else "ASSISTANT"
        snippet = content[:2000]  # cap individual messages
        lines.append(f"[{role}]: {snippet}")
        total += len(snippet)
    return "\n\n".join(lines)


def get_existing_decisions() -> str:
    """Read existing decision log to avoid duplicates."""
    if not DECISION_LOG.exists():
        return ""
    return DECISION_LOG.read_text()[-3000:]  # last ~3000 chars for dedup context


def analyze_decisions(conversation_text: str, existing: str) -> list[dict]:
    """Use Claude to extract decisions from conversation text."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"Extract meaningful decisions from this conversation log. "
                f"Skip anything already captured in the existing log.\n\n"
                f"--- EXISTING LOG (last entries, for dedup) ---\n{existing}\n\n"
                f"--- CONVERSATION LOG ---\n{conversation_text}"
            ),
        }],
    )
    text = response.content[0].text.strip()
    # Find JSON array in response
    start = text.find("[")
    end = text.rfind("]") + 1
    if start == -1 or end == 0:
        return []
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return []


def append_decisions(decisions: list[dict]):
    """Append decisions to the decision log."""
    today = datetime.now().strftime("%Y-%m-%d")
    DECISION_LOG.parent.mkdir(parents=True, exist_ok=True)

    # Read existing content
    existing = ""
    if DECISION_LOG.exists():
        existing = DECISION_LOG.read_text()

    # Build new entries
    entries = []
    for d in decisions:
        entry = (
            f"### {d['title']}\n"
            f"**Domain:** {d['domain']}\n"
            f"**Decision:** {d['decision']}\n"
            f"**Why:** {d['why']}\n"
            f"**Alternatives considered:** {d['alternatives']}\n"
        )
        entries.append(entry)

    if not entries:
        return

    new_section = f"## {today}\n\n" + "\n".join(entries)

    # Check if today's header already exists
    if f"## {today}" in existing:
        # Append under existing date header
        existing = existing.replace(
            f"## {today}\n",
            f"## {today}\n\n" + "\n".join(entries),
            1,
        )
        DECISION_LOG.write_text(existing)
    else:
        # Insert after frontmatter
        if existing.startswith("---"):
            # Find end of frontmatter
            end_fm = existing.find("---", 3)
            if end_fm != -1:
                insert_pos = existing.find("\n", end_fm) + 1
                updated = existing[:insert_pos] + "\n" + new_section + "\n" + existing[insert_pos:]
                DECISION_LOG.write_text(updated)
                return

        # No frontmatter or new file
        if not existing:
            header = (
                "---\n"
                "title: Decision Log\n"
                f"date: {today}\n"
                "type: reference\n"
                "tags: [g245, decisions]\n"
                "status: active\n"
                "---\n\n"
            )
            DECISION_LOG.write_text(header + new_section + "\n")
        else:
            DECISION_LOG.write_text(new_section + "\n\n" + existing)


def main():
    logs = find_recent_logs()
    if not logs:
        return

    # Combine recent conversations
    all_text = ""
    for log in logs[:5]:  # cap at 5 most recent
        text = extract_conversation_text(log)
        if text:
            all_text += f"\n\n=== Session: {log.name} ===\n\n{text}"

    if len(all_text) < 200:
        return

    existing = get_existing_decisions()
    decisions = analyze_decisions(all_text, existing)

    if decisions:
        append_decisions(decisions)
        print(f"Captured {len(decisions)} decision(s)")
    else:
        print("No new decisions found")


if __name__ == "__main__":
    main()
