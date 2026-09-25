#!/usr/bin/env python3
"""singleton-audit.py — weekly vault folder-hygiene audit.

Rule (vault/CLAUDE.md, set 2026-09-24): a folder earns its existence at
about 3 docs. Flags every folder under ~/vault that holds fewer than 3 files
(not counting its own folder note) and has no subfolders, writes a dated
report to z_meta/, and files ONE Todoist task whose *description* carries
the findings — dtd's agent gesture on that task injects the description
into the Claude prompt ("Context from Todoist"), so starting the task opens
a session that already knows what to walk through with you.

Usage:
    singleton-audit.py            # write report + create/refresh the Todoist task
    singleton-audit.py --dry-run  # print findings only

Idempotent: if an open task with the same content already exists, its
description is updated in place rather than a second task created; if
nothing is flagged, no task is created and any open one is closed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import os
import re
import sys
from pathlib import Path

VAULT = Path.home() / "vault"
REPORT_DIR = VAULT / "z_meta"
THRESHOLD = 3
AUTO_MARK = "😈"  # created-by-a-robot marker (see stale-contacts.py)
TASK_CONTENT = f"{AUTO_MARK} vault singleton-folder review (20) [15]"
TASK_LABELS = ["i447"]

# Folders never audited: system dirs, mirrors of external systems, archives,
# and per-entry trees whose shape is dictated by something else.
EXCLUDE_PREFIXES = (
    ".", "z_asts", "z_arcv", "z_ibx", "z_meta",
    "i447/i446/ai-transcripts", "i447/i446/dream-runs", "i447/i446/i446-monorepo",
    "h335/m5x2/m5x2-m",            # Google Drive mirror (PDF-heavy, not authored here)
    "hcmc/readwise",               # auto-synced
    "hcmp/o315/blog",              # Hugo site tree
    "g245/archive", "g245/5e-1",   # build-order daily archives (one folder per day by design)
    "d357",                        # janus meeting recordings, one folder per week by design
)
# Folder names that are dates are per-period buckets, never singletons.
DATE_DIR_RE = re.compile(r"^\d{4}([.\-]\d{2}){0,2}$|^\d{4}\.\d{1,2}$")
# m5x2 per-property folders (a210, r202, rl16, ...) are one-folder-per-asset by
# design (z_meta "Vault Structure"); a note-only property folder is normal.
PROPERTY_DIR_RE = re.compile(r"^(?:[a-z]\d{3}|rl\d{2})$")
EXCLUDE_NAMES = {"node_modules", "__pycache__", ".git", ".obsidian", "venv", ".venv"}
DOC_EXTS = {".md", ".pdf", ".docx", ".xlsx", ".csv", ".txt", ".html", ".json", ".png", ".jpg", ".jpeg"}

_DF_PATH = Path.home() / "i446-monorepo/tools/did/defer-fast.py"


def _todoist():
    spec = importlib.util.spec_from_file_location("defer_fast_for_audit", _DF_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["defer_fast_for_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


def excluded(rel: str) -> bool:
    parts = rel.split("/")
    if any(p in EXCLUDE_NAMES or p.startswith(".") or DATE_DIR_RE.match(p) for p in parts):
        return True
    if rel.startswith("h335/m5x2/") and PROPERTY_DIR_RE.match(parts[-1]):
        return True
    return any(rel == e or rel.startswith(e.rstrip("/") + "/") for e in EXCLUDE_PREFIXES)


def scan(vault: Path = VAULT) -> list[dict]:
    """Return [{rel, files:[names], note: folder-note-name-or-None}] for every
    flagged folder, sorted by path."""
    out = []
    for root, dirs, files in os.walk(vault):
        rel = os.path.relpath(root, vault)
        if rel == ".":
            dirs[:] = [d for d in dirs if not excluded(d)]
            continue
        if excluded(rel):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not excluded(f"{rel}/{d}")]
        if dirs:                       # has subfolders → structural, not a singleton
            continue
        docs = sorted(f for f in files if not f.startswith(".")
                      and Path(f).suffix.lower() in DOC_EXTS)
        base = os.path.basename(root)
        note = next((f for f in docs if Path(f).stem == base), None)
        content_docs = [f for f in docs if f != note]
        if len(content_docs) < THRESHOLD:
            out.append({"rel": rel, "files": content_docs, "note": note})
    return sorted(out, key=lambda r: r["rel"])


def suggest(r: dict) -> str:
    n = len(r["files"])
    if n == 0 and r["note"]:
        return "note only: lift the note up a level, drop the folder"
    if n == 0:
        return "empty: delete"
    return "flatten (prefix + move up) or merge into a sibling, then drop the folder"


def build_report(rows: list[dict], today: dt.date) -> str:
    lines = ["---", f'title: "Singleton Folder Audit {today.isoformat()}"',
             f"date: {today.isoformat()}", "type: audit", "tags: [z_meta, audit, i447]",
             "source: singleton-audit.py", "status: active", "---",
             f"Folders with fewer than {THRESHOLD} docs (folder note excluded) and no subfolders. "
             "Rule: ~3 docs before a folder (vault/CLAUDE.md). Work through with `/open` on each row.",
             ""]
    if not rows:
        lines.append("None. Vault is clean.")
        return "\n".join(lines) + "\n"
    lines += ["| # | Folder | Docs | Suggestion |", "|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        docs = ", ".join(r["files"]) or "(none)"
        if r["note"]:
            docs += f" (+ note {r['note']})"
        lines.append(f"| {i} | `{r['rel']}/` | {docs} | {suggest(r)} |")
    return "\n".join(lines) + "\n"


def build_description(rows: list[dict], report_rel: str) -> str:
    """Todoist description = the context dtd hands to the Claude agent."""
    head = [f"Weekly vault folder-hygiene audit. Report: {report_rel}",
            f"{len(rows)} folder(s) under the 3-doc rule. Walk through them WITH me, "
            "numbered, one decision each (flatten / merge / delete / keep-as-exception). "
            "Don't move anything until I answer. Add confirmed exceptions to "
            "tools/vault/singleton-audit.py EXCLUDE_PREFIXES.", ""]
    body = [f"{i}. {r['rel']}/ — {', '.join(r['files']) or 'note only'}"
            for i, r in enumerate(rows, 1)]
    text = "\n".join(head + body)
    return text[:15000]


def upsert_task(rows: list[dict], report_rel: str, today: dt.date) -> str:
    td = _todoist()
    # Filter search paginates; a flat GET /tasks?limit=200 silently misses
    # the task once there are >200 open tasks (duplicate created 2026-09-24).
    items = td._fetch_tasks("search: vault singleton-folder review")
    existing = next((t for t in items if isinstance(t, dict)
                     and t.get("content", "").lstrip(AUTO_MARK).strip()
                         .startswith("vault singleton-folder review")), None)
    if not rows:
        if existing:
            td.close_task(existing["id"])
            return f"clean; closed stale task {existing['id']}"
        return "clean; no task"
    desc = build_description(rows, report_rel)
    if existing:
        td._api("POST", f"/tasks/{existing['id']}", {"description": desc, "due_date": today.isoformat()})
        return f"updated task {existing['id']}"
    t = td._api("POST", "/tasks", {"content": TASK_CONTENT, "labels": TASK_LABELS,
                                   "due_date": today.isoformat(), "description": desc,
                                   "duration": 20, "duration_unit": "minute"})
    return f"created task {t.get('id') if isinstance(t, dict) else '?'}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    today = dt.date.today()
    rows = scan()
    report = build_report(rows, today)
    if a.dry_run:
        print(report)
        return 0
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"{today.isoformat()}-singleton-folders.md"
    path.write_text(report)
    rel = str(path.relative_to(VAULT))
    try:
        status = upsert_task(rows, rel, today)
    except Exception as e:  # noqa: BLE001
        status = f"todoist failed: {e}"
    print(f"{today} {len(rows)} flagged → {rel}; {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
