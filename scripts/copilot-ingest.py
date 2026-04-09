#!/usr/bin/env python3
"""Ingest GitHub Copilot agent session logs into llm-sessions.db.

Parses process-*.log files from both ~/.copilot/logs/ and ~/.agency/logs/
for assistant_usage and session_usage_info telemetry events, groups by
session_id, and upserts into the sessions table (provider='copilot', product='agent').

Designed to run periodically (e.g. every 30 min via cron).
"""

import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

COPILOT_LOGS = Path.home() / ".copilot" / "logs"
AGENCY_LOGS = Path.home() / ".agency" / "logs"
DB_PATH = Path.home() / "vault" / "i447" / "i446" / "llm-sessions.db"
MARKER_FILE = COPILOT_LOGS / ".ingested_files"


def load_ingested():
    """Track which log files have already been fully ingested."""
    if MARKER_FILE.exists():
        return set(MARKER_FILE.read_text().strip().splitlines())
    return set()


def save_ingested(files):
    MARKER_FILE.write_text("\n".join(sorted(files)) + "\n")


def extract_json_blocks(log_path):
    """Extract top-level JSON objects from a Copilot log file.

    Log format: lines starting with timestamps, interspersed with
    multi-line JSON blocks after '[INFO] [Telemetry]' lines.
    """
    blocks = []
    current_block = []
    in_block = False
    brace_depth = 0

    with open(log_path) as f:
        for line in f:
            stripped = line.rstrip()

            if not in_block:
                # JSON blocks start with a bare '{'
                if stripped == "{":
                    in_block = True
                    brace_depth = 1
                    current_block = [stripped]
                continue

            current_block.append(stripped)
            brace_depth += stripped.count("{") - stripped.count("}")

            if brace_depth <= 0:
                raw = "\n".join(current_block)
                try:
                    obj = json.loads(raw)
                    kind = obj.get("kind", "")
                    if kind in ("assistant_usage", "session_usage_info"):
                        blocks.append(obj)
                except json.JSONDecodeError:
                    pass
                in_block = False
                current_block = []
                brace_depth = 0

    return blocks


def build_sessions(blocks):
    """Group telemetry blocks by session_id into session summaries."""
    sessions = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "message_count": 0,
        "model": None,
        "start_time": None,
        "end_time": None,
        "duration_ms": 0,
    })

    for block in blocks:
        sid = block.get("session_id")
        if not sid:
            continue

        s = sessions[sid]
        ts = block.get("created_at")

        if ts:
            if s["start_time"] is None or ts < s["start_time"]:
                s["start_time"] = ts
            if s["end_time"] is None or ts > s["end_time"]:
                s["end_time"] = ts

        kind = block.get("kind")
        metrics = block.get("metrics", {})
        props = block.get("properties", {})

        if kind == "assistant_usage":
            s["input_tokens"] += metrics.get("input_tokens", 0)
            s["output_tokens"] += metrics.get("output_tokens", 0)
            s["duration_ms"] += metrics.get("duration", 0)
            s["message_count"] += 1
            model = props.get("model")
            if model:
                s["model"] = model

        elif kind == "session_usage_info":
            msg_len = metrics.get("messages_length", 0)
            if msg_len > s["message_count"]:
                s["message_count"] = msg_len

    return sessions


def upsert_sessions(sessions):
    """Upsert sessions into llm-sessions.db."""
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}", file=sys.stderr)
        return 0

    conn = sqlite3.connect(str(DB_PATH))
    count = 0

    for sid, s in sessions.items():
        if not s["start_time"]:
            continue

        # Use copilot-agent- prefix to avoid colliding with copilot-track.sh entries
        db_sid = f"copilot-agent-{sid[:12]}"

        conn.execute("""
            INSERT INTO sessions
                (session_id, provider, product, model, start_time, end_time,
                 message_count, input_tokens, output_tokens, total_tokens,
                 cost_usd, status, user_id)
            VALUES (?, 'copilot', 'agent', ?, ?, ?, ?, ?, ?, ?, 0.0, 'completed', 'jm')
            ON CONFLICT(session_id) DO UPDATE SET
                end_time = excluded.end_time,
                message_count = MAX(sessions.message_count, excluded.message_count),
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                total_tokens = excluded.total_tokens,
                model = COALESCE(excluded.model, sessions.model)
        """, (
            db_sid,
            s["model"],
            s["start_time"],
            s["end_time"],
            s["message_count"],
            s["input_tokens"],
            s["output_tokens"],
            s["input_tokens"] + s["output_tokens"],
        ))
        count += 1

    conn.commit()
    conn.close()
    return count


def main():
    # Collect log files from both ~/.copilot/logs/ and ~/.agency/logs/
    log_files = []
    if COPILOT_LOGS.exists():
        log_files.extend(sorted(COPILOT_LOGS.glob("process-*.log")))
    if AGENCY_LOGS.exists():
        # Agency logs are in session subdirectories
        log_files.extend(sorted(AGENCY_LOGS.glob("*/process-*.log")))

    if not log_files:
        print("No Copilot/Agency log files found", file=sys.stderr)
        return

    ingested = load_ingested()

    all_blocks = []
    newly_processed = set()

    for lf in log_files:
        fname = str(lf)  # use full path as key since files span directories
        blocks = extract_json_blocks(lf)
        if blocks:
            all_blocks.extend(blocks)
            newly_processed.add(lf.name)

    if not all_blocks:
        print("No usage events found")
        return

    sessions = build_sessions(all_blocks)
    count = upsert_sessions(sessions)

    # Save all processed files (always re-process all files since sessions span files)
    save_ingested(ingested | newly_processed)

    print(f"Ingested {count} Copilot agent sessions from {len(newly_processed)} log files")
    for sid, s in sorted(sessions.items(), key=lambda x: x[1]["start_time"] or ""):
        ot = s["output_tokens"]
        mc = s["message_count"]
        m = s["model"] or "?"
        print(f"  {sid[:12]}  {m:>10}  {mc:>3} turns  {ot:>8} out_tok  {s['start_time']}")


if __name__ == "__main__":
    main()
