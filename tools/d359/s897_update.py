#!/usr/bin/env python3
"""s897_update.py — edit d359 people-database metadata from /s897 shorthand.

    python3 s897_update.py "Jessica Allen met yesterday"
    python3 s897_update.py "Jessica Allen cadence monthly"
    python3 s897_update.py "Jessica Allen role Chief of Staff"
    python3 s897_update.py --dry-run "..."

Input = <person> <update>. The person is resolved against ~/vault/d359/ by
longest filename/title prefix match, so multi-word names never need quoting.

Updates:
    met [today|yesterday|M/D|YYYY-MM-DD]
        Set last_contact (default today) + updated, then DELETE any open
        😈-prefixed Todoist task labelled d359/<slug> — the robot outreach
        reminder is moot once contact actually happened (user 2026-07-21:
        "remove the task saying that I should reach out"). Hand-written tasks
        for the contact (no 😈) are left alone, and deletion means no points
        are claimed for a task the robot invented.
    <field> <value>
        Set any scalar frontmatter field (cadence, role, relationship,
        organization, location, status, outreach_task, work_email, ...).
        The field must already exist in the file OR be in COMMON_FIELDS —
        a typo'd verb must not silently become a new frontmatter key.

Exit codes: 0 ok, 1 no/ambiguous person or bad update, 2 environment error.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))

D359 = Path.home() / "vault/d359"
AUTO_MARK = "😈"
COMMON_FIELDS = {
    "cadence", "last_contact", "relationship", "role", "organization",
    "location", "status", "outreach_task", "work_email", "teams_upn",
    "email", "phone", "birthday", "partner", "kids",
}
SKIP_FILES = {"claude.md", "d359-index.md", "d359.md"}


def _bare(stem: str) -> str:
    """d359 filename stem → person name key: 'jessica-allen-d359' /
    'Jordan Allen d359' → 'jessica allen' / 'jordan allen'."""
    s = re.sub(r"[-\s]d359$", "", stem, flags=re.I)
    return re.sub(r"[-_\s]+", " ", s).strip().lower()


def _slug(path: Path) -> str:
    """Todoist label slug (stale-contacts convention): filename minus the
    d359 suffix, spaces normalized to hyphens: 'jessica-allen'."""
    s = re.sub(r"[-\s]d359$", "", path.stem, flags=re.I)
    return re.sub(r"[\s_]+", "-", s.strip()).lower()


def resolve_person(words: list[str]) -> tuple[Path | None, int, list[str]]:
    """Longest prefix of `words` matching a d359 file. Returns
    (path, words_consumed, candidates_on_ambiguity)."""
    files = [p for p in D359.glob("*.md") if p.name.lower() not in SKIP_FILES]
    by_key: dict[str, list[Path]] = {}
    for p in files:
        by_key.setdefault(_bare(p.stem), []).append(p)
    for n in range(min(4, len(words)), 0, -1):
        key = " ".join(w.lower() for w in words[:n])
        hits = by_key.get(key)
        if hits:
            return (hits[0], n, []) if len(hits) == 1 else (None, n, [str(h) for h in hits])
    # Fallback: unique substring match on the first token (e.g. just "jessica")
    tok = words[0].lower()
    subs = [p for k, ps in by_key.items() if tok in k for p in ps]
    if len(subs) == 1:
        return subs[0], 1, []
    return None, 0, [str(p) for p in subs]


def parse_date(tokens: list[str]) -> date | None:
    if not tokens or tokens[0].lower() == "today":
        return date.today()
    t = tokens[0].lower()
    if t == "yesterday":
        return date.today() - timedelta(days=1)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", t)
    if m:
        d = date(date.today().year, int(m.group(1)), int(m.group(2)))
        return d if d <= date.today() else d.replace(year=d.year - 1)
    return None


def patch_field(text: str, key: str, value: str) -> str | None:
    """Set `key: value` in the frontmatter block; insert before the closing
    --- when absent. None when the file has no frontmatter."""
    m = re.match(r"^---\n(.*?\n)---\n", text, re.S)
    if not m:
        return None
    fm = m.group(1)
    line = f"{key}: {value}"
    pat = re.compile(rf"^{re.escape(key)}:\s*.*$", re.M)
    new_fm = pat.sub(line, fm) if pat.search(fm) else fm.rstrip("\n") + f"\n{line}\n"
    return text[:m.start(1)] + new_fm + text[m.end(1):]


def delete_robot_outreach(slug: str, dry: bool) -> list[str]:
    """Delete open 😈 tasks labelled d359/<slug>; return their contents."""
    import todoist
    gone = []
    for t in todoist.find_tasks(labels=[f"d359/{slug}"], limit=50):
        content = t.get("content") or ""
        if not content.startswith(AUTO_MARK):
            continue  # hand-written task — never a robot's to delete
        if not dry:
            todoist._request("DELETE", f"/tasks/{t['id']}")
        gone.append(content)
    return gone


def main() -> int:
    args = sys.argv[1:]
    dry = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    if not args:
        print("usage: s897_update.py [--dry-run] \"<person> <update>\"", file=sys.stderr)
        return 2
    words = " ".join(args).split()

    person, used, candidates = resolve_person(words)
    if person is None:
        if candidates:
            print("ambiguous person, candidates:\n  " + "\n  ".join(candidates), file=sys.stderr)
        else:
            print(f"no d359 match for: {' '.join(words)}", file=sys.stderr)
        return 1
    update = words[used:]
    if not update:
        print(f"{person.name}: no update given", file=sys.stderr)
        return 1

    text = person.read_text()
    verb = update[0].lower()
    out: list[str] = []

    if verb == "met":
        when = parse_date(update[1:])
        if when is None:
            print(f"cannot parse date: {' '.join(update[1:])}", file=sys.stderr)
            return 1
        text = patch_field(text, "last_contact", when.isoformat())
        if text is None:
            print(f"{person.name}: no frontmatter block", file=sys.stderr)
            return 2
        text = patch_field(text, "updated", date.today().isoformat()) or text
        out.append(f"last_contact → {when.isoformat()}")
        try:
            gone = delete_robot_outreach(_slug(person), dry)
            out += [f"deleted {AUTO_MARK} task: {g}" for g in gone] or ["no open robot outreach task"]
        except Exception as e:  # noqa: BLE001 — Todoist down must not lose the vault write
            out.append(f"⚠ todoist cleanup failed: {e}")
    else:
        # Generic field set: known key (existing in file or whitelisted).
        key = verb
        m = re.match(r"^---\n(.*?\n)---\n", text, re.S)
        known = key in COMMON_FIELDS or (m and re.search(rf"^{re.escape(key)}:", m.group(1), re.M))
        if not known:
            print(f"unknown update '{key}' — use met/<frontmatter field> "
                  f"(known: {', '.join(sorted(COMMON_FIELDS))})", file=sys.stderr)
            return 1
        value = " ".join(update[1:]).strip()
        if not value:
            print(f"no value for field '{key}'", file=sys.stderr)
            return 1
        text = patch_field(text, key, value)
        if text is None:
            print(f"{person.name}: no frontmatter block", file=sys.stderr)
            return 2
        text = patch_field(text, "updated", date.today().isoformat()) or text
        out.append(f"{key} → {value}")

    if not dry:
        person.write_text(text)
    prefix = "DRY " if dry else ""
    print(f"{prefix}{_slug(person)}: " + " · ".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
