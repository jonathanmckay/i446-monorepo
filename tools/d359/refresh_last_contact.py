#!/usr/bin/env python3
"""refresh_last_contact.py — Auto-derive d359 last_contact from passive signals.

For every d359 person file, compute:
    effective_last_contact = max(
        manual last_contact (floor),
        latest Toggl entry tagged d359/<slug>,
        latest d358 meeting note that mentions the slug,
    )

If the derived value is newer than the manual value, patch the frontmatter
in place. Manual entries are never lowered — they're treated as a floor.

Future signal sources (not yet wired):
    - Gmail / Outlook sent mail to channels.email / channels.work_email
    - iMessage to channels.phone
    - Slack DM to channels.slack
    - Google Calendar events with the person as attendee

Usage:
    python3 refresh_last_contact.py              # dry-run report
    python3 refresh_last_contact.py --apply      # patch frontmatter
    python3 refresh_last_contact.py --person <slug>  # one person only
    python3 refresh_last_contact.py --days 365   # lookback window
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # i446-monorepo
sys.path.insert(0, str(ROOT / "mcp"))


def _ensure_toggl_env():
    """Load TOGGL_API_KEY from ~/.claude.json MCP config if env is unset."""
    import json
    import os
    if os.environ.get("TOGGL_API_KEY"):
        return
    claude_json = Path.home() / ".claude.json"
    if not claude_json.exists():
        return
    try:
        data = json.loads(claude_json.read_text())
        key = (data.get("mcpServers", {})
                   .get("toggl_server", {})
                   .get("env", {})
                   .get("TOGGL_API_KEY", ""))
        if key:
            os.environ["TOGGL_API_KEY"] = key
    except Exception:
        pass


_ensure_toggl_env()

VAULT = Path.home() / "vault"
D359_DIR = VAULT / "d359"
D358_DIR = VAULT / "h335" / "d358"

# Files in d359/ that aren't person docs
SKIP_NAMES = {"CLAUDE.md", "d359-index.md", "CONTEXT.md", "README.md"}


# ── Frontmatter parsing ─────────────────────────────────────────────────────

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_LAST_CONTACT_RE = re.compile(r"^last_contact:\s*(\S+)\s*$", re.MULTILINE)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_frontmatter(text: str):
    """Return (frontmatter_text, rest_text) or (None, text) if absent."""
    m = _FM_RE.match(text)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def _read_last_contact(fm: str):
    if not fm:
        return None
    m = _LAST_CONTACT_RE.search(fm)
    if not m:
        return None
    raw = m.group(1).strip().strip('"').strip("'")
    if not _DATE_RE.match(raw):
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _patch_last_contact(text: str, new_date: date) -> str:
    """Return text with last_contact set to new_date. If field exists, replace.
    If absent but frontmatter exists, insert before the closing ---. If no
    frontmatter at all, leave text unchanged (return as-is)."""
    fm, rest = _parse_frontmatter(text)
    if fm is None:
        return text
    new_val = new_date.isoformat()
    if _LAST_CONTACT_RE.search(fm):
        new_fm = _LAST_CONTACT_RE.sub(f"last_contact: {new_val}", fm)
    else:
        new_fm = fm.rstrip() + f"\nlast_contact: {new_val}"
    return f"---\n{new_fm}\n---\n{rest}"


# ── Slug extraction ─────────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"^(.+?)-d359(?:\.md)?$")


def _slug_from_filename(path: Path) -> str | None:
    """Extract canonical slug from a d359 filename.

    'jessica-allen-d359.md' → 'jessica-allen'
    '何阿姨-d359.md'         → '何阿姨'
    'Louisa Xu d359.md'      → 'louisa-xu' (lowercase, hyphenate spaces)
    """
    stem = path.stem  # filename without .md
    m = _SLUG_RE.match(stem)
    if not m:
        return None
    raw = m.group(1).strip().rstrip("-").rstrip()
    # Normalize legacy "Firstname Lastname" → canonical lowercase-hyphenated
    normalized = re.sub(r"\s+", "-", raw).lower()
    return normalized or None


# ── Toggl signal ────────────────────────────────────────────────────────────

def _build_unambiguous_token_map(slugs: set[str]):
    """Return ({token: slug} for unambiguous first-name tokens, aliases dict).
    Excludes short tokens and stopwords to avoid noise."""
    _STOPWORDS = {"the", "and", "for", "with", "to", "of", "in", "on", "at",
                  "a", "an", "1", "2", "3", "old", "new"}
    first_tokens: dict[str, list[str]] = {}
    for slug in slugs:
        tokens = [t for t in slug.split("-") if t and t not in _STOPWORDS]
        if not tokens:
            continue
        ft = tokens[0]
        if len(ft) < 3:
            continue
        first_tokens.setdefault(ft, []).append(slug)
    unambiguous = {ft: ss[0] for ft, ss in first_tokens.items() if len(ss) == 1}
    aliases = {"lx": "louisa-xu", "lr": "leeroy-phillips", "hz": "hanzhao"}
    for alias, target in aliases.items():
        if target in slugs:
            unambiguous[alias] = target
    return unambiguous


def _fetch_toggl_signal(days: int, slugs: set[str]) -> dict[str, date]:
    """Return {slug: latest_date} from Toggl.

    Matches in two ways:
      1. Explicit tag 'd359/<slug>' (canonical convention; rarely used today)
      2. Description token matches a slug's first-name token IF that token is
         unambiguous across all d359 slugs (avoids 'Ian' colliding 5 ways)

    One API call covers everyone. Silently returns {} on failure."""
    try:
        from toggl_server import toggl_api
    except ImportError:
        return {}
    # Toggl /me/time_entries caps at ~1000 entries per call. Chunk by 30 days
    # to safely cover long windows for high-volume trackers.
    chunk_days = 30
    entries = []
    end_d = date.today() + timedelta(days=1)
    cursor = end_d
    target = end_d - timedelta(days=days)
    while cursor > target:
        chunk_start = max(target, cursor - timedelta(days=chunk_days))
        try:
            batch = toggl_api.get_entries(
                start_date=chunk_start.isoformat(),
                end_date=cursor.isoformat(),
            )
        except Exception as exc:
            print(f"warn: toggl fetch {chunk_start}..{cursor} failed: {exc}",
                  file=sys.stderr)
            break
        if isinstance(batch, list):
            entries.extend(batch)
        cursor = chunk_start

    # Build unambiguous first-token → slug map (shared with d358 scan).
    unambiguous = _build_unambiguous_token_map(slugs)

    latest: dict[str, date] = {}

    def _bump(slug: str, d: date):
        if slug not in latest or d > latest[slug]:
            latest[slug] = d

    word_re = re.compile(r"[\w]+", re.UNICODE)
    for entry in entries:
        start_iso = entry.get("start")
        if not start_iso:
            continue
        try:
            d = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        # Canonical tag form
        for tag in entry.get("tags") or []:
            if tag.startswith("d359/"):
                slug = tag[5:].strip().lower()
                if slug:
                    _bump(slug, d)
        # Description token match
        desc = (entry.get("description") or "").lower()
        if not desc:
            continue
        for word in word_re.findall(desc):
            slug = unambiguous.get(word)
            if slug:
                _bump(slug, d)
    return latest


# ── d358 mention signal ─────────────────────────────────────────────────────

_DATE_IN_PATH_RE = re.compile(r"(20\d{2})[.\-](\d{1,2})[.\-](\d{1,2})")


def _extract_date_from_path(path: Path) -> date | None:
    """Extract a YYYY.MM.DD or YYYY-MM-DD from filename or parent dir."""
    m = _DATE_IN_PATH_RE.search(path.name) or _DATE_IN_PATH_RE.search(str(path))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


_FRONTMATTER_DATE_RE = re.compile(r"^date:\s*(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})",
                                  re.MULTILINE)


def _extract_date_from_content(content: str) -> date | None:
    """Pull a date from YAML frontmatter `date:` field if present."""
    head = content[:500]
    m = _FRONTMATTER_DATE_RE.search(head)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _fetch_d358_signal(slugs: set[str], days: int) -> dict[str, date]:
    """Scan d358 meeting notes for person references.

    Matches:
      1. '<slug>-d359' canonical reference (explicit wikilink target)
      2. Unambiguous first-name token (e.g. 'Stuart' if only one Stuart exists)

    Date source: filename prefix > YAML frontmatter `date:` field. mtime is
    NOT used as a fallback (bulk git syncs would flood updates with the sync
    date). Files without a derivable date are skipped. Returns {slug: date}."""
    if not D358_DIR.exists() or not slugs:
        return {}
    cutoff_date = date.today() - timedelta(days=days)
    today = date.today()
    latest: dict[str, date] = {}
    unambiguous = _build_unambiguous_token_map(slugs)
    canonical = {slug: re.compile(rf"\b{re.escape(slug)}-d359\b", re.IGNORECASE)
                 for slug in slugs}
    word_re = re.compile(r"\b[\w]+\b", re.UNICODE)

    for path in D358_DIR.rglob("*.md"):
        file_date = _extract_date_from_path(path)
        content = None
        if not file_date:
            try:
                content = path.read_text(errors="ignore")
            except OSError:
                continue
            file_date = _extract_date_from_content(content)
        if not file_date or file_date > today or file_date < cutoff_date:
            continue
        if content is None:
            try:
                content = path.read_text(errors="ignore")
            except OSError:
                continue
        content_lower = content.lower()

        if "d359" in content_lower:
            for slug, pat in canonical.items():
                if pat.search(content):
                    if slug not in latest or file_date > latest[slug]:
                        latest[slug] = file_date

        hits = set()
        for word in word_re.findall(content_lower):
            slug = unambiguous.get(word)
            if slug:
                hits.add(slug)
        for slug in hits:
            if slug not in latest or file_date > latest[slug]:
                latest[slug] = file_date

    return latest


# ── Main ────────────────────────────────────────────────────────────────────

def _collect_person_files(filter_slug: str | None = None) -> list[Path]:
    files = []
    for path in D359_DIR.iterdir():
        if not path.is_file() or path.suffix != ".md":
            continue
        if path.name in SKIP_NAMES:
            continue
        slug = _slug_from_filename(path)
        if not slug:
            continue
        if filter_slug and slug != filter_slug.lower():
            continue
        files.append(path)
    return sorted(files)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Write changes back to disk (default: dry-run)")
    ap.add_argument("--person", help="Only process this slug")
    ap.add_argument("--days", type=int, default=400,
                    help="Lookback window in days (default 400)")
    ap.add_argument("--verbose", action="store_true",
                    help="Print every file, including unchanged ones")
    args = ap.parse_args(argv)

    files = _collect_person_files(args.person)
    if not files:
        print("no d359 person files matched")
        return 1
    slugs = {_slug_from_filename(p) for p in files}
    slugs.discard(None)

    print(f"scanning {len(files)} d359 person files (lookback {args.days}d)")
    toggl_sig = _fetch_toggl_signal(args.days, slugs)
    print(f"  toggl: {len(toggl_sig)} slugs with recent activity")
    d358_sig = _fetch_d358_signal(slugs, args.days)
    print(f"  d358:  {len(d358_sig)} slugs mentioned in recent notes")
    print()

    updates = []
    for path in files:
        slug = _slug_from_filename(path)
        if not slug:
            continue
        try:
            text = path.read_text()
        except OSError as exc:
            print(f"  ! cannot read {path.name}: {exc}", file=sys.stderr)
            continue
        fm, _ = _parse_frontmatter(text)
        manual = _read_last_contact(fm) if fm else None
        candidates = {"manual": manual,
                      "toggl": toggl_sig.get(slug),
                      "d358": d358_sig.get(slug)}
        non_null = {k: v for k, v in candidates.items() if v}
        if not non_null:
            if args.verbose:
                print(f"  · {slug:40} (no signals)")
            continue
        best_source, best_date = max(non_null.items(), key=lambda kv: kv[1])
        if manual and best_date <= manual:
            if args.verbose:
                print(f"  = {slug:40} manual {manual} ≥ derived {best_date} [{best_source}]")
            continue
        delta = (best_date - manual).days if manual else None
        delta_str = f"+{delta}d" if delta is not None else "new"
        print(f"  ↑ {slug:40} {manual} → {best_date} [{best_source}] ({delta_str})")
        updates.append((path, text, best_date))

    print()
    print(f"would update {len(updates)} of {len(files)} files")
    if not args.apply:
        print("(dry-run; pass --apply to write changes)")
        return 0
    written = 0
    for path, text, new_date in updates:
        new_text = _patch_last_contact(text, new_date)
        if new_text == text:
            print(f"  ! {path.name} unchanged after patch (no frontmatter?)", file=sys.stderr)
            continue
        path.write_text(new_text)
        written += 1
    print(f"wrote {written} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
