#!/usr/bin/env python3
"""Track Dream card approval rates for self-calibration.

Maintains a JSON log of card outcomes and provides summary statistics
that Dream can use to tune its ranker weights.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path.home() / "vault/i447/i446/dream-runs/approval-log.json"

VALID_OUTCOMES = {"approved", "rejected", "deferred", "edited", "held", "auto-dropped"}
VALID_GRADES = {"A", "B", "C"}
VALID_FOLDS = {"above", "below"}


def load_log() -> dict:
    if not LOG_PATH.exists():
        return {"entries": []}
    with open(LOG_PATH) as f:
        return json.load(f)


def save_log(data: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=LOG_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, LOG_PATH)
    except BaseException:
        os.unlink(tmp)
        raise


def cmd_log(args: argparse.Namespace) -> None:
    if args.outcome not in VALID_OUTCOMES:
        print(f"Invalid outcome: {args.outcome}. Must be one of: {', '.join(sorted(VALID_OUTCOMES))}", file=sys.stderr)
        sys.exit(1)
    if args.grade not in VALID_GRADES:
        print(f"Invalid grade: {args.grade}. Must be one of: {', '.join(sorted(VALID_GRADES))}", file=sys.stderr)
        sys.exit(1)
    if args.fold not in VALID_FOLDS:
        print(f"Invalid fold: {args.fold}. Must be one of: {', '.join(sorted(VALID_FOLDS))}", file=sys.stderr)
        sys.exit(1)

    data = load_log()
    entry = {
        "run": args.run,
        "card_num": args.card,
        "title": args.title,
        "domain": args.domain,
        "grade": args.grade,
        "fold": args.fold,
        "outcome": args.outcome,
        "points": args.points,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if args.response:
        entry["jm_response"] = args.response
    data["entries"].append(entry)
    save_log(data)
    print(f"Logged card #{args.card} from {args.run}: {args.outcome}")


def approval_rate(entries: list[dict]) -> float | None:
    if not entries:
        return None
    approved = sum(1 for e in entries if e["outcome"] == "approved")
    return approved / len(entries)


def fmt_pct(rate: float | None) -> str:
    if rate is None:
        return "n/a"
    return f"{rate:.0%}"


def cmd_stats(args: argparse.Namespace) -> None:
    data = load_log()
    entries = data["entries"]
    if not entries:
        print("No data yet.")
        return

    entries = entries[-args.last:]
    n = len(entries)
    print(f"=== Dream Approval Stats (last {n} entries) ===\n")

    # Overall
    overall = approval_rate(entries)
    print(f"Overall approval rate: {fmt_pct(overall)} ({sum(1 for e in entries if e['outcome'] == 'approved')}/{n})")

    # By domain
    by_domain = defaultdict(list)
    for e in entries:
        by_domain[e["domain"]].append(e)
    print("\nBy domain:")
    for domain in sorted(by_domain):
        group = by_domain[domain]
        rate = approval_rate(group)
        print(f"  {domain}: {fmt_pct(rate)} ({len(group)} cards)")

    # By grade
    by_grade = defaultdict(list)
    for e in entries:
        by_grade[e["grade"]].append(e)
    print("\nBy grade:")
    for grade in sorted(by_grade):
        group = by_grade[grade]
        rate = approval_rate(group)
        print(f"  {grade}: {fmt_pct(rate)} ({len(group)} cards)")

    # By fold
    by_fold = defaultdict(list)
    for e in entries:
        by_fold[e["fold"]].append(e)
    print("\nBy fold:")
    for fold in sorted(by_fold):
        group = by_fold[fold]
        rate = approval_rate(group)
        print(f"  {fold}: {fmt_pct(rate)} ({len(group)} cards)")

    # Average points approved vs rejected
    approved_pts = [e["points"] for e in entries if e["outcome"] == "approved" and e.get("points") is not None]
    rejected_pts = [e["points"] for e in entries if e["outcome"] == "rejected" and e.get("points") is not None]
    print("\nAverage points:")
    if approved_pts:
        print(f"  Approved: {sum(approved_pts) / len(approved_pts):.1f}")
    else:
        print("  Approved: n/a")
    if rejected_pts:
        print(f"  Rejected: {sum(rejected_pts) / len(rejected_pts):.1f}")
    else:
        print("  Rejected: n/a")

    # Flags
    low = [d for d, g in by_domain.items() if (r := approval_rate(g)) is not None and r < 0.3]
    high = [d for d, g in by_domain.items() if (r := approval_rate(g)) is not None and r > 0.8]
    if low:
        print(f"\n⚠ Low approval (<30%): {', '.join(sorted(low))} — consider deprioritizing")
    if high:
        print(f"\n✓ High approval (>80%): {', '.join(sorted(high))} — candidate for auto-execute")


def cmd_calibrate(args: argparse.Namespace) -> None:
    data = load_log()
    entries = data["entries"]
    if not entries:
        print(json.dumps({"boost": [], "dampen": [], "auto_approve": [], "drop": []}, indent=2))
        return

    # Group by domain
    by_domain = defaultdict(list)
    for e in entries:
        by_domain[e["domain"]].append(e)

    # Group by (domain, grade) for finer-grained signals
    by_category = defaultdict(list)
    for e in entries:
        by_category[(e["domain"], e["grade"])].append(e)

    boost = []
    dampen = []
    for domain, group in by_domain.items():
        rate = approval_rate(group)
        if rate is not None and rate > 0.8 and len(group) >= 3:
            boost.append({"domain": domain, "approval_rate": round(rate, 3), "n": len(group)})
        elif rate is not None and rate < 0.3 and len(group) >= 3:
            dampen.append({"domain": domain, "approval_rate": round(rate, 3), "n": len(group)})

    auto_approve = []
    drop = []
    for (domain, grade), group in by_category.items():
        rate = approval_rate(group)
        if rate is not None and rate == 1.0 and len(group) >= 10:
            auto_approve.append({"domain": domain, "grade": grade, "n": len(group)})
        elif rate is not None and rate == 0.0 and len(group) >= 5:
            drop.append({"domain": domain, "grade": grade, "n": len(group)})

    result = {
        "boost": sorted(boost, key=lambda x: -x["approval_rate"]),
        "dampen": sorted(dampen, key=lambda x: x["approval_rate"]),
        "auto_approve": sorted(auto_approve, key=lambda x: -x["n"]),
        "drop": sorted(drop, key=lambda x: -x["n"]),
    }
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Track Dream card approval rates for self-calibration")
    subs = parser.add_subparsers(dest="command", required=True)

    # log
    p_log = subs.add_parser("log", help="Add a new entry")
    p_log.add_argument("--run", required=True, help="Run identifier (e.g. v7)")
    p_log.add_argument("--card", type=int, required=True, help="Card number")
    p_log.add_argument("--title", required=True, help="Card title")
    p_log.add_argument("--domain", required=True, help="Domain code (e.g. m5x2, i9)")
    p_log.add_argument("--grade", required=True, help="Grade: A, B, or C")
    p_log.add_argument("--fold", required=True, help="Fold: above or below")
    p_log.add_argument("--outcome", required=True, help="Outcome: approved, rejected, deferred, edited, held, auto-dropped")
    p_log.add_argument("--points", type=int, default=0, help="Point value (default: 0)")
    p_log.add_argument("--response", default=None, help="JM's response text")

    # stats
    p_stats = subs.add_parser("stats", help="Print summary statistics")
    p_stats.add_argument("--last", type=int, default=50, help="Number of recent entries to consider (default: 50)")

    # calibrate
    subs.add_parser("calibrate", help="Output JSON recommendations for ranker weight adjustments")

    args = parser.parse_args()
    if args.command == "log":
        cmd_log(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "calibrate":
        cmd_calibrate(args)


if __name__ == "__main__":
    main()
