#!/usr/bin/env python3
"""dream-rem1: free exploration phase of Dream.

Reads a random sample of vault markdown files, finds non-obvious connections
between them, and outputs a JSON array of insight cards for downstream
synthesis by an LLM pass.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

EXCLUDE_DIRS = {"z_asts", ".obsidian", "ai-transcripts"}
EXCLUDE_PREFIXES = ("z_ibx/archive",)

DOMAIN_CODES = [
    "d359", "g245", "h335", "m5x2", "qz12", "hcmc", "hcmp", "hcbi",
    "xk88", "xk87", "s897", "i447", "i446", "i444", "i9", "m828",
    "o314", "d357", "d358", "q5n7", "h5c7", "f8", "hcb", "hcm",
    "epcn", "n156",
]
DOMAIN_RE = re.compile(r"\b(" + "|".join(DOMAIN_CODES) + r")\b")

DOLLAR_RE = re.compile(r"\$[\d,]+(?:\.\d+)?[BMKbmk]?")
DATE_RE = re.compile(r"\b(\d{4}[-\.]\d{2}[-\.]\d{2})\b")
NAME_FIELD_RE = re.compile(r"^\*\*(?:Name|Role|name|role):\*\*\s*(.+)", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
TAG_RE = re.compile(r"#([a-zA-Z][\w/-]+)")
PROPER_NOUN_RE = re.compile(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)\b")


def collect_md_files(vault: Path) -> list[Path]:
    results = []
    for root, dirs, files in os.walk(vault):
        rel = Path(root).relative_to(vault)
        rel_str = str(rel)
        dirs[:] = [
            d for d in dirs
            if d not in EXCLUDE_DIRS
            and not any(rel_str.startswith(p) or f"{rel_str}/{d}".startswith(p)
                        for p in EXCLUDE_PREFIXES)
        ]
        for f in files:
            if f.endswith(".md"):
                results.append(Path(root) / f)
    return results


def weighted_sample(files: list[Path], n: int, recent_days: int = 30) -> list[Path]:
    now = time.time()
    cutoff = now - recent_days * 86400
    weights = []
    for f in files:
        try:
            mtime = f.stat().st_mtime
        except OSError:
            mtime = 0
        weights.append(3.0 if mtime > cutoff else 1.0)
    n = min(n, len(files))
    return random.choices(files, weights=weights, k=n) if n > 0 else []


def extract_frontmatter(text: str) -> dict[str, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def domain_from_path(path: Path, vault: Path) -> str:
    rel = path.relative_to(vault)
    parts = rel.parts
    for p in parts:
        if p in DOMAIN_CODES:
            return p
        for code in DOMAIN_CODES:
            if p.startswith(code):
                return code
    return parts[0] if parts else "unknown"


def extract_entities(text: str, path: Path, vault: Path) -> dict[str, Any]:
    fm = extract_frontmatter(text)
    title = fm.get("title", path.stem)
    domain = domain_from_path(path, vault)
    tags = TAG_RE.findall(text)
    domains_mentioned = list(set(DOMAIN_RE.findall(text)))
    dollars = DOLLAR_RE.findall(text)
    dates = DATE_RE.findall(text)
    names_from_fields = NAME_FIELD_RE.findall(text)
    proper_nouns = list(set(PROPER_NOUN_RE.findall(text)))
    is_d359 = "d359" in str(path.relative_to(vault))
    d359_name = path.stem.replace("-", " ").title() if is_d359 else None

    all_names = list(set(names_from_fields + ([d359_name] if d359_name else [])))

    return {
        "path": str(path),
        "title": title,
        "domain": domain,
        "tags": tags,
        "domains_mentioned": domains_mentioned,
        "dollars": dollars,
        "dates": dates,
        "names": all_names,
        "proper_nouns": proper_nouns,
        "mtime": path.stat().st_mtime,
    }


def find_cross_domain_names(entities: list[dict]) -> list[dict]:
    name_domains: dict[str, list[dict]] = defaultdict(list)
    for e in entities:
        for name in e["names"] + e["proper_nouns"]:
            name_lower = name.lower()
            if len(name_lower) < 4:
                continue
            name_domains[name_lower].append(e)

    cards = []
    for name, appearances in name_domains.items():
        domains = set(a["domain"] for a in appearances)
        if len(domains) >= 2:
            files = list(set(a["path"] for a in appearances))
            cards.append({
                "type": "connection",
                "files": files,
                "summary": f"'{name}' appears across domains: {', '.join(sorted(domains))}",
                "confidence": min(0.9, 0.4 + 0.1 * len(domains)),
                "suggested_action": f"Review whether '{name}' references need alignment across these domains.",
            })
    return cards


def find_dollar_contradictions(entities: list[dict]) -> list[dict]:
    dollar_vals: dict[str, list[tuple[float, dict]]] = defaultdict(list)
    for e in entities:
        for d in e["dollars"]:
            raw = d.replace("$", "").replace(",", "")
            multiplier = 1.0
            if raw.endswith(("B", "b")):
                multiplier = 1e9
                raw = raw[:-1]
            elif raw.endswith(("M", "m")):
                multiplier = 1e6
                raw = raw[:-1]
            elif raw.endswith(("K", "k")):
                multiplier = 1e3
                raw = raw[:-1]
            try:
                val = float(raw) * multiplier
            except ValueError:
                continue
            context = e["title"].lower()
            dollar_vals[context].append((val, e))

    cards = []
    for context, vals in dollar_vals.items():
        if len(vals) < 2:
            continue
        amounts = [v[0] for v in vals]
        if max(amounts) == 0:
            continue
        spread = (max(amounts) - min(amounts)) / max(amounts)
        if spread > 0.2:
            files = list(set(v[1]["path"] for v in vals))
            amounts_str = ", ".join(f"${v[0]:,.0f}" for v in vals)
            cards.append({
                "type": "contradiction",
                "files": files,
                "summary": f"Dollar amounts for '{context}' differ >20%: {amounts_str}",
                "confidence": min(0.85, 0.5 + spread * 0.3),
                "suggested_action": f"Verify which dollar figure for '{context}' is current.",
            })
    return cards


def find_recurring_themes(entities: list[dict]) -> list[dict]:
    tag_domains: dict[str, set[str]] = defaultdict(set)
    tag_files: dict[str, list[str]] = defaultdict(list)
    for e in entities:
        for tag in e["tags"]:
            tag_domains[tag].add(e["domain"])
            tag_files[tag].append(e["path"])

    cards = []
    for tag, domains in tag_domains.items():
        if len(domains) >= 3:
            cards.append({
                "type": "pattern",
                "files": list(set(tag_files[tag])),
                "summary": f"Tag '#{tag}' recurs across {len(domains)} domains: {', '.join(sorted(domains))}",
                "confidence": min(0.8, 0.3 + 0.1 * len(domains)),
                "suggested_action": f"Consider whether '#{tag}' warrants a dedicated tracking note or project.",
            })
    return cards


def find_d359_in_journal(entities: list[dict]) -> list[dict]:
    d359_names: dict[str, str] = {}
    for e in entities:
        if e["domain"] == "d359":
            for name in e["names"]:
                d359_names[name.lower()] = e["path"]

    cards = []
    for e in entities:
        if e["domain"] != "o314":
            continue
        for name in e["proper_nouns"]:
            if name.lower() in d359_names:
                cards.append({
                    "type": "connection",
                    "files": [d359_names[name.lower()], e["path"]],
                    "summary": f"Contact '{name}' mentioned in journal entry '{e['title']}'",
                    "confidence": 0.7,
                    "suggested_action": f"Update d359 profile for '{name}' with recent interaction date.",
                })
    return cards


def find_stale_files(entities: list[dict]) -> list[dict]:
    now = time.time()
    stale_threshold = 180 * 86400
    cards = []
    for e in entities:
        age_days = (now - e["mtime"]) / 86400
        if age_days > 180 and e["tags"]:
            cards.append({
                "type": "stale",
                "files": [e["path"]],
                "summary": f"'{e['title']}' ({e['domain']}) untouched for {int(age_days)} days but has active tags: {', '.join(e['tags'][:5])}",
                "confidence": min(0.7, 0.3 + age_days / 1000),
                "suggested_action": "Review whether this file is still relevant or should be archived.",
            })
    return cards


def find_domain_bridges(entities: list[dict]) -> list[dict]:
    cards = []
    for e in entities:
        foreign = [d for d in e["domains_mentioned"] if d != e["domain"]]
        if len(foreign) >= 3:
            cards.append({
                "type": "connection",
                "files": [e["path"]],
                "summary": f"'{e['title']}' in {e['domain']} references {len(foreign)} other domains: {', '.join(sorted(foreign))}",
                "confidence": min(0.75, 0.4 + 0.05 * len(foreign)),
                "suggested_action": "This file may be a cross-cutting concern; consider linking it from referenced domains.",
            })
    return cards


def run(vault_path: str, sample_size: int, output: str | None) -> list[dict]:
    vault = Path(vault_path).expanduser().resolve()
    if not vault.is_dir():
        print(f"Error: vault path '{vault}' is not a directory", file=sys.stderr)
        sys.exit(1)

    all_files = collect_md_files(vault)
    sampled = weighted_sample(all_files, sample_size)
    sampled = list({str(f): f for f in sampled}.values())

    entities = []
    for f in sampled:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        entities.append(extract_entities(text, f, vault))

    cards: list[dict] = []
    cards.extend(find_cross_domain_names(entities))
    cards.extend(find_dollar_contradictions(entities))
    cards.extend(find_recurring_themes(entities))
    cards.extend(find_d359_in_journal(entities))
    cards.extend(find_stale_files(entities))
    cards.extend(find_domain_bridges(entities))

    cards.sort(key=lambda c: c["confidence"], reverse=True)

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(cards, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(cards, indent=2, ensure_ascii=False))

    return cards


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Dream REM-1: free exploration of vault connections"
    )
    parser.add_argument(
        "--vault-path", default="~/vault/",
        help="Path to vault root (default: ~/vault/)"
    )
    parser.add_argument(
        "--sample-size", type=int, default=20,
        help="Number of files to sample (default: 20)"
    )
    parser.add_argument(
        "--output", default=None,
        help="Path to write JSON output (default: stdout)"
    )
    args = parser.parse_args()
    run(args.vault_path, args.sample_size, args.output)
