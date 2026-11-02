#!/usr/bin/env python3
"""Generate live weekly_data.json from the ExP MCP server.

This script is the live-data counterpart to the renderer in build_weekly_cards.py.
It pulls the three step buckets from the ExP MCP server, enriches each unique
experiment and feature, computes next-step names for stopped steps, and writes
weekly_data.json next to the renderer.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests

ROOT = Path(__file__).parent
MCP_URL = "https://exp.azure.net/mcp"
MCP_SCOPE = "api://d9473dd8-6329-4dcc-83fd-1bcbe4a531df/mcp"
WORKSPACE_ID = "3e3b8347-c494-4f7e-a5aa-3e693ba7dd3c"
PAGE_SIZE = 100
MCP_TIMEOUT_SECONDS = 300


def compute_week_window(today: date | None = None) -> tuple[str, str]:
    """Return last Monday..Sunday in ISO-date format."""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    last_monday = monday - timedelta(days=7)
    last_sunday = last_monday + timedelta(days=6)
    return last_monday.isoformat(), last_sunday.isoformat()


def format_week_label(week_start: str, week_end: str) -> str:
    start = datetime.fromisoformat(week_start).date()
    end = datetime.fromisoformat(week_end).date()
    return f"{start:%b} {start.day} – {end:%b} {end.day}, {end.year}"


def dedupe_buckets(started: list[dict[str, Any]], stopped: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Remove steps that started and stopped this week from the started bucket."""
    stopped_ids = {item.get("stepId") or item.get("id") for item in stopped if item.get("stepId") or item.get("id")}
    filtered_started = [item for item in started if (item.get("stepId") or item.get("id")) not in stopped_ids]
    return filtered_started, stopped


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        ts = ts.replace("Z", "+00:00")
        # Normalize fractional seconds to 6 digits for Python <3.11 compatibility.
        ts = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], ts)
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def next_step_name(step: dict[str, Any], experiment_steps: list[dict[str, Any]]) -> str | None:
    """Determine the next step name for a stopped step."""
    current_stop = parse_iso(step.get("stoppedAt"))
    if current_stop is None:
        return None

    candidates = []
    for candidate in experiment_steps:
        started = parse_iso(candidate.get("startedAt"))
        if started and started > current_stop:
            candidates.append((started, candidate))

    if not candidates:
        return None

    _, next_step = min(candidates, key=lambda item: item[0])
    display_name = next_step.get("displayName") or next_step.get("stepName") or ""
    analysis_type = next_step.get("analysisType") or ""
    if analysis_type == "AB":
        return f"{display_name} (50/50 AB)" if "50/50" in (next_step.get("splits") or "") else f"{display_name} (AB)"
    if analysis_type == "DataCollection":
        return f"{display_name} (100% DC)" if "100" in (next_step.get("splits") or "") else f"{display_name} (DC)"
    return display_name


def format_splits(step: dict[str, Any]) -> str:
    """Convert variant traffic percentages into a friendly split label."""
    maps = step.get("variantTrafficMaps") or []
    if not maps:
        return "100%" if step.get("analysisType") == "DataCollection" else "—"

    values = sorted({int(round(float(item.get("trafficExposurePercentage", 0)))) for item in maps if item.get("trafficExposurePercentage") is not None})
    if not values:
        return "—"
    if len(values) == 1:
        return f"{values[0]}%"
    return "/".join(str(value) for value in values)


def normalize_step(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "stepId": step.get("id") or step.get("stepId"),
        "experimentId": step.get("experimentId"),
        "stepName": step.get("displayName") or step.get("stepName") or "",
        "startedAt": step.get("startedAt"),
        "stoppedAt": step.get("stoppedAt"),
        "analysisType": step.get("analysisType") or "",
        "splits": format_splits(step),
    }


def get_access_token() -> str:
    az_path = shutil.which("az.cmd") or shutil.which("az")
    if not az_path:
        raise RuntimeError("Azure CLI was not found on PATH.")

    command = [
        az_path,
        "account",
        "get-access-token",
        "--scope",
        MCP_SCOPE,
        "--query",
        "accessToken",
        "-o",
        "tsv",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError("Unable to acquire an ExP MCP bearer token. Run 'az login' first.\n" + result.stderr.strip())
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("Azure CLI returned an empty access token for the ExP MCP scope.")
    return token


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid4()),
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    response = requests.post(MCP_URL, headers=headers, json=payload, timeout=MCP_TIMEOUT_SECONDS)
    if response.status_code != 200:
        raise RuntimeError(f"MCP call failed with HTTP {response.status_code}: {response.text[:800]}")

    data_chunks = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    if not data_chunks:
        raise RuntimeError("MCP response did not contain any data frames.")

    payload = json.loads("".join(data_chunks))
    if "error" in payload:
        raise RuntimeError(f"MCP tool error: {payload['error']}")

    content = payload.get("result", {}).get("content") or []
    text_blocks = [item.get("text", "") for item in content if item.get("type") == "text"]
    if not text_blocks:
        return {}

    combined = "".join(text_blocks)
    if not combined.strip():
        return {}

    return json.loads(combined)


def query_steps(odata_filter: str, step_type: str = "ExperimentStep") -> list[dict[str, Any]]:
    # The MCP server may silently cap responses below the requested `top`
    # (we've observed 10/page even when asking for 100). Paginate based on the
    # ACTUAL number of items returned and only stop on an empty response, so we
    # never assume the server honored our requested page size.
    results: list[dict[str, Any]] = []
    skip = 0
    print(f"  query_steps({step_type}): {odata_filter}", flush=True)
    while True:
        response = call_tool(
            "query_experiment_entity",
            {
                "workspaceId": WORKSPACE_ID,
                "useMicrosoftCredential": True,
                "type": step_type,
                "odataFilter": odata_filter,
                "skip": skip,
                "top": PAGE_SIZE,
                "simplifyResponse": True,
            },
        )
        items = response.get("value") or []
        if not items:
            break
        results.extend(items)
        skip += len(items)
        print(f"    page returned {len(items)} (total so far: {len(results)})", flush=True)
    print(f"  -> {len(results)} total", flush=True)
    return results


def normalize_entity_response(response: dict[str, Any]) -> dict[str, Any]:
    """Normalize the MCP tool response to the canonical entity object shape."""
    if not isinstance(response, dict):
        return {}

    items = response.get("value")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]

    if any(key in response for key in ("id", "displayName", "featureId", "experimentId", "audienceFilterExpression")):
        return response

    return {}


def query_entity(entity_type: str, entity_id: str) -> dict[str, Any]:
    response = call_tool(
        "query_experiment_entity",
        {
            "workspaceId": WORKSPACE_ID,
            "useMicrosoftCredential": True,
            "type": entity_type,
            "entityId": entity_id,
            "simplifyResponse": True,
        },
    )
    return normalize_entity_response(response)


# Parallelism for entity enrichment: each query_entity call is an independent
# network request, so we fan out with a small thread pool. Keep concurrency
# modest to stay polite to the MCP server. Override with EXP_MCP_WORKERS env var.
ENRICHMENT_WORKERS = int(os.environ.get("EXP_MCP_WORKERS", "8"))
_progress_lock = threading.Lock()


def _experiment_record(experiment: dict[str, Any]) -> dict[str, Any]:
    return {
        "featureId": experiment.get("featureId"),
        "displayName": experiment.get("displayName") or "",
        "audience": (experiment.get("audienceFilterExpression") or "")[:160],
        "steps": experiment.get("experimentSteps") or [],
    }


def _feature_record(feature: dict[str, Any]) -> dict[str, Any]:
    return {
        "displayName": feature.get("displayName") or "",
        "description": feature.get("description") or "",
        "experimentationGroup": feature.get("experimentationGroup") or "",
    }


def enrich_in_parallel(entity_type: str, ids: list[str], record_fn) -> dict[str, dict[str, Any]]:
    total = len(ids)
    out: dict[str, dict[str, Any]] = {}
    if total == 0:
        return out
    print(f"Enriching {total} unique {entity_type.lower()}s (workers={ENRICHMENT_WORKERS})...", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=ENRICHMENT_WORKERS) as pool:
        futures = {pool.submit(query_entity, entity_type, entity_id): entity_id for entity_id in ids}
        for future in as_completed(futures):
            entity_id = futures[future]
            entity = future.result()
            if entity:
                out[entity_id] = record_fn(entity)
            with _progress_lock:
                done += 1
                if done == 1 or done % 25 == 0 or done == total:
                    print(f"  {entity_type.lower()} {done}/{total}", flush=True)
    # Re-key in the original id order so downstream output is deterministic.
    return {entity_id: out[entity_id] for entity_id in ids if entity_id in out}


def query_scorecard_availability(step_ids: list[str]) -> set[str]:
    """Query ExP MCP for which step IDs have valid scorecards.

    Uses query_analyses with type=ExperimentStepAnalysis per step.
    A step has a valid scorecard when any analysis in the response has
    latestScorecardInfo.scorecardId set AND latestScorecardInfo.state == "Succeeded".
    """
    if not step_ids:
        return set()

    print(f"Checking scorecard availability for {len(step_ids)} steps...", flush=True)
    has_scorecard: set[str] = set()
    errors = 0
    done = 0

    def _check_one(step_id: str) -> str | None:
        try:
            response = call_tool(
                "query_analyses",
                {
                    "workspaceId": WORKSPACE_ID,
                    "useMicrosoftCredential": True,
                    "type": "ExperimentStepAnalysis",
                    "experimentStepId": step_id,
                    "simplifyResponse": True,
                },
            )
            if not response:
                return None
            items = response.get("value", []) if isinstance(response, dict) else []
            for item in items:
                sc_info = item.get("latestScorecardInfo") or {}
                if sc_info.get("scorecardId") and sc_info.get("state") == "Succeeded":
                    return step_id
        except Exception:
            return None
        return None

    with ThreadPoolExecutor(max_workers=ENRICHMENT_WORKERS) as pool:
        futures = {pool.submit(_check_one, sid): sid for sid in step_ids}
        for future in as_completed(futures):
            result = future.result()
            if result:
                has_scorecard.add(result)
            done += 1
            if done == 1 or done % 25 == 0 or done == len(step_ids):
                print(f"  scorecard check {done}/{len(step_ids)}", flush=True)

    if has_scorecard:
        print(f"  -> {len(has_scorecard)}/{len(step_ids)} steps have valid scorecards", flush=True)
    else:
        print("  -> no valid scorecards found, using heuristic fallback", flush=True)

    return has_scorecard


def build_weekly_data(week_start: str, week_end: str) -> dict[str, Any]:
    started_raw = query_steps(f"StartedAt ge {week_start}T00:00:00Z and StartedAt le {week_end}T23:59:59Z")
    stopped_raw = query_steps(f"StoppedAt ge {week_start}T00:00:00Z and StoppedAt le {week_end}T23:59:59Z")

    # The live server can be conservative with filters, so we keep the raw lists
    # and let the dedupe logic remove overlap between the two buckets.
    started = [normalize_step(item) for item in started_raw]
    stopped = [normalize_step(item) for item in stopped_raw]
    started, stopped = dedupe_buckets(started, stopped)

    active_raw = query_steps(
        f"StartedAt lt {week_start}T00:00:00Z and StoppedAt eq null and AnalysisType eq 'AB'",
        step_type="ExperimentStep",
    )
    active = [normalize_step(item) for item in active_raw]

    experiment_ids = OrderedDict.fromkeys([item["experimentId"] for item in started + stopped + active if item.get("experimentId")])
    experiments = enrich_in_parallel("Experiment", list(experiment_ids), _experiment_record)

    feature_ids = OrderedDict.fromkeys([experiments[item]["featureId"] for item in experiments if experiments[item].get("featureId")])
    features = enrich_in_parallel("Feature", list(feature_ids), _feature_record)

    stopped_with_next = []
    for step in stopped:
        experiment = experiments.get(step["experimentId"])
        next_name = next_step_name(step, experiment.get("steps", []) if experiment else []) if experiment else None
        stopped_with_next.append({**step, "nextStepName": next_name})

    # Query scorecard availability for all steps
    all_step_ids = [s["stepId"] for s in started + stopped_with_next + active if s.get("stepId")]
    steps_with_scorecards = query_scorecard_availability(all_step_ids)

    # Only annotate steps with hasScorecard if the query returned results.
    # If empty (query failed), omit the field so the renderer uses its heuristic.
    if steps_with_scorecards:
        def _annotate(steps: list[dict]) -> list[dict]:
            for s in steps:
                s["hasScorecard"] = s.get("stepId", "") in steps_with_scorecards
            return steps

        _annotate(started)
        _annotate(stopped_with_next)
        _annotate(active)

    return {
        "week_label": format_week_label(week_start, week_end),
        "week_start": week_start,
        "week_end": week_end,
        "generated_for_date": date.today().isoformat(),
        "features": features,
        "experiments": {experiment_id: {"featureId": meta["featureId"], "displayName": meta["displayName"], "audience": meta["audience"]} for experiment_id, meta in experiments.items()},
        "buckets": {
            "started": started,
            "stopped": stopped_with_next,
            "active": active,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate live weekly_data.json from ExP MCP")
    parser.add_argument("--input", default=str(ROOT / "weekly_data.json"), help="Output path for weekly_data.json")
    parser.add_argument("--render", action="store_true", help="Render the HTML dashboard after writing the data file")
    args = parser.parse_args()

    week_start, week_end = compute_week_window()
    output_path = Path(args.input)
    payload = build_weekly_data(week_start, week_end)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {output_path}")
    print(f"Week window: {week_start} -> {week_end}")
    print(f"Started buckets: {len(payload['buckets']['started'])}")
    print(f"Stopped buckets: {len(payload['buckets']['stopped'])}")
    print(f"Active buckets: {len(payload['buckets']['active'])}")

    if args.render:
        renderer = ROOT / "build_weekly_cards.py"
        if not renderer.exists():
            raise FileNotFoundError("Renderer not found: build_weekly_cards.py")
        subprocess.run([sys.executable, str(renderer)], check=True)
        print(f"Rendered {ROOT / 'weekly_dashboard.html'}")


if __name__ == "__main__":
    main()
