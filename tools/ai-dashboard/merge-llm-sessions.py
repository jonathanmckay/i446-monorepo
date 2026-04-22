#!/usr/bin/env python3
"""merge-llm-sessions.py — merge Copilot CLI (and other non-Claude provider)
session stats from llm-sessions.db into a Claude Code stats-cache.json.

Background: the m5x2 AI dashboard reads dailyActivity / dailyModelTokens from
stats-cache.json, which Claude Code generates from its own JSONL session files.
Copilot CLI sessions are tracked separately by ~/i446-monorepo/scripts/copilot-ingest.py
(which writes to ~/vault/i447/i446/llm-sessions.db with provider='copilot').

Without this merge, anything done in Copilot CLI is invisible to the dashboard.

This script:
  1. Loads the source stats-cache.json (Claude Code's view).
  2. Reads llm-sessions.db for non-claude providers (default: copilot).
  3. Adds per-day messageCount/sessionCount and per-day per-model token counts.
     Copilot model keys are namespaced as "copilot/<model>" so they don't
     collide with Claude Code entries.
  4. Recomputes summary fields (totalSessions, totalMessages, modelUsage,
     hourCounts when possible) so the dashboard sees consistent totals.
  5. Writes the merged result to the output path.

Idempotent: each run starts from the source file fresh, so repeated runs
produce the same output.
"""

from __future__ import annotations

import argparse
import copy
import json
import socket
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DEFAULT_SRC = Path.home() / ".claude" / "stats-cache.json"
DEFAULT_DB = Path.home() / "vault" / "i447" / "i446" / "llm-sessions.db"
DEFAULT_OUT = Path.home() / "m5x2-ai-stats" / "jm" / "stats-cache.json"

# Providers to fold into the dashboard alongside Claude Code.
# 'claude' is excluded — already represented in the source stats-cache.json.
EXTRA_PROVIDERS = ("copilot",)


def _local_date(ts: str) -> str:
    """Convert an ISO-ish timestamp from llm-sessions.db to a YYYY-MM-DD date.

    llm-sessions.db stores timestamps as either UTC (with 'Z' / '+00:00') or
    naive local. We normalize to local date by parsing and using astimezone()
    when a tzinfo is present.
    """
    if not ts:
        return ""
    try:
        # Common SQLite formats: 'YYYY-MM-DD HH:MM:SS' or ISO 8601
        s = ts.replace("Z", "+00:00").replace(" ", "T", 1)
        dt = datetime.fromisoformat(s)
    except ValueError:
        return ts[:10]
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d")


def fetch_provider_aggregates(db_path: Path, providers=EXTRA_PROVIDERS):
    """Return aggregated per-day per-model data from llm-sessions.db.

    Returns:
      {
        date: {
          'sessions': int,
          'messages': int,
          'tokens_by_model': {model: int, ...},
        }, ...
      }
    Plus a separate per-model lifetime aggregate for `modelUsage`.
    """
    if not db_path.exists():
        return {}, {}

    placeholders = ",".join("?" * len(providers))
    query = f"""
        SELECT start_time, provider, model, message_count, total_tokens
        FROM sessions
        WHERE provider IN ({placeholders})
    """
    daily = defaultdict(lambda: {"sessions": 0, "messages": 0, "tokens_by_model": defaultdict(int)})
    model_totals = defaultdict(lambda: {"messages": 0, "tokens": 0, "sessions": 0})

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        for start_time, provider, model, msgs, toks in conn.execute(query, providers):
            date = _local_date(start_time)
            if not date:
                continue
            model_key = f"{provider}/{model or 'unknown'}"
            daily[date]["sessions"] += 1
            daily[date]["messages"] += int(msgs or 0)
            daily[date]["tokens_by_model"][model_key] += int(toks or 0)
            model_totals[model_key]["sessions"] += 1
            model_totals[model_key]["messages"] += int(msgs or 0)
            model_totals[model_key]["tokens"] += int(toks or 0)

    # Convert defaultdicts to plain dicts for JSON-friendliness
    out_daily = {
        date: {
            "sessions": v["sessions"],
            "messages": v["messages"],
            "tokens_by_model": dict(v["tokens_by_model"]),
        }
        for date, v in daily.items()
    }
    return out_daily, dict(model_totals)


def merge(stats: dict, daily_extra: dict, model_extra: dict, device: str | None = None) -> dict:
    """Merge extra-provider data into a copy of stats. Returns the merged dict."""
    merged = copy.deepcopy(stats)

    # ── dailyActivity ────────────────────────────────────────────────────
    da_list = merged.get("dailyActivity", []) or []
    da_index = {e["date"]: e for e in da_list if isinstance(e, dict) and "date" in e}
    for date, agg in daily_extra.items():
        entry = da_index.get(date)
        if entry is None:
            entry = {"date": date, "messageCount": 0, "sessionCount": 0, "toolCallCount": 0}
            da_list.append(entry)
            da_index[date] = entry
        entry["messageCount"] = entry.get("messageCount", 0) + agg["messages"]
        entry["sessionCount"] = entry.get("sessionCount", 0) + agg["sessions"]
        # toolCallCount: no reliable source for non-Claude — leave untouched
    da_list.sort(key=lambda e: e.get("date", ""))
    merged["dailyActivity"] = da_list

    # ── dailyModelTokens ─────────────────────────────────────────────────
    dmt_list = merged.get("dailyModelTokens", []) or []
    dmt_index = {e["date"]: e for e in dmt_list if isinstance(e, dict) and "date" in e}
    for date, agg in daily_extra.items():
        entry = dmt_index.get(date)
        if entry is None:
            entry = {"date": date, "tokensByModel": {}}
            dmt_list.append(entry)
            dmt_index[date] = entry
        tbm = entry.setdefault("tokensByModel", {})
        for model, toks in agg["tokens_by_model"].items():
            # Each merge run replaces the namespaced model entry (idempotent).
            tbm[model] = toks
    dmt_list.sort(key=lambda e: e.get("date", ""))
    merged["dailyModelTokens"] = dmt_list

    # ── modelUsage ───────────────────────────────────────────────────────
    mu = merged.setdefault("modelUsage", {})
    for model, totals in model_extra.items():
        mu[model] = {
            "sessionCount": totals["sessions"],
            "messageCount": totals["messages"],
            "totalTokens": totals["tokens"],
        }

    # ── totals ──────────────────────────────────────────────────────────
    extra_sessions = sum(v["sessions"] for v in model_extra.values())
    extra_messages = sum(v["messages"] for v in model_extra.values())
    if "totalSessions" in merged:
        merged["totalSessions"] = (stats.get("totalSessions", 0) or 0) + extra_sessions
    if "totalMessages" in merged:
        merged["totalMessages"] = (stats.get("totalMessages", 0) or 0) + extra_messages

    merged["mergedProviders"] = list(EXTRA_PROVIDERS)
    merged["mergedAt"] = datetime.now().isoformat(timespec="seconds")

    # ── deviceActivity ──────────────────────────────────────────────────
    # Tag each dailyActivity entry with the device name so the dashboard
    # can show a Turns / Device breakdown.
    if device:
        da_device = []
        for entry in merged.get("dailyActivity", []):
            da_device.append({
                "date": entry["date"],
                "device": device,
                "messageCount": entry.get("messageCount", 0),
                "sessionCount": entry.get("sessionCount", 0),
                "toolCallCount": entry.get("toolCallCount", 0),
            })
        merged["deviceActivity"] = da_device

    return merged


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC,
                    help=f"Claude Code stats-cache.json (default: {DEFAULT_SRC})")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB,
                    help=f"llm-sessions.db (default: {DEFAULT_DB})")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"output path for merged stats (default: {DEFAULT_OUT})")
    ap.add_argument("--device", type=str,
                    default=socket.gethostname().split(".")[0].lower(),
                    help="device/hostname tag for deviceActivity entries")
    args = ap.parse_args()

    if not args.src.exists():
        print(f"error: source stats not found: {args.src}", file=sys.stderr)
        return 1

    stats = json.loads(args.src.read_text())
    daily_extra, model_extra = fetch_provider_aggregates(args.db)
    merged = merge(stats, daily_extra, model_extra, device=args.device)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(merged, indent=2, ensure_ascii=False))

    extra_sessions = sum(v["sessions"] for v in model_extra.values())
    extra_messages = sum(v["messages"] for v in model_extra.values())
    extra_tokens = sum(v["tokens"] for v in model_extra.values())
    print(f"merged → {args.out}")
    print(f"  +{extra_sessions} sessions, +{extra_messages} messages, "
          f"+{extra_tokens:,} tokens from {','.join(EXTRA_PROVIDERS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
