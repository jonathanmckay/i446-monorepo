#!/usr/bin/env python3
"""refresh_last_contact.py — Auto-derive d359 last_contact from passive signals.

For every d359 person file, compute:
    effective_last_contact = max(
        manual last_contact (floor),
        latest Toggl entry tagged d359/<slug>,
        latest d358 meeting note that mentions the slug,
        latest Google Calendar event matched to the person,
    )

If the derived value is newer than the manual value, patch the frontmatter
in place. Manual entries are never lowered — they're treated as a floor.

Calendar matching is high-precision by design. The MSFT (Slow Sync) feed is an
ICS import that STRIPS attendees, so its events expose only a title; it is also
full of org-wide noise (OOF notices, standups) whose titles mention people you
never met that day. We therefore match calendar events two safe ways only:
    1. attendee email == a contact's channels.{work_email,teams_upn,email}
       (works on genuinely-invited events, e.g. your primary calendar)
    2. the 1:1 title convention '<INITIALS>:JM' / 'JM:<INITIALS>' (also '|'),
       resolved through an UNAMBIGUOUS-initials map (if two contacts share
       initials it matches neither — fail-safe, mirrors the Toggl/d358 logic)
We deliberately do NOT do generic first-name token matching on titles: too many
false bumps from the import feed's noise.

Future signal sources (not yet wired):
    - Gmail / Outlook sent mail to channels.email / channels.work_email
    - iMessage to channels.phone
    - Slack DM to channels.slack

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


# ── Google Calendar signal ──────────────────────────────────────────────────

# MSFT (Slow Sync) ICS import + primary m5c7 calendar. The import feed carries
# the work 1:1s (attendees stripped → matched by title); primary carries the
# occasional genuinely-invited event (matched by attendee email).
GCAL_ACCOUNT = "m5c7"
GCAL_CAL_IDS = [
    "l20n3a79v2lq68fod4de3lvp1ba2iqft@import.calendar.google.com",  # MSFT Slow Sync
    "mckay@m5c7.com",                                                # primary
]
_GCAL_OAUTH_KEYS = Path.home() / ".config" / "google-calendar-mcp" / "gcp-oauth.keys.json"
_GCAL_TOKENS = Path.home() / ".config" / "google-calendar-mcp" / "tokens.json"

# Work addresses ONLY. A generic personal `email:` (e.g. a spouse's gmail) shows
# up as an attendee on family/personal events ('Imperial treasure', kid camps),
# which are not outreach; matching those would falsely bump and suppress real
# overdue reminders. Work email / Teams UPN attendance is a genuine meeting.
_EMAIL_FM_RE = re.compile(
    r"^\s*(?:work_email|teams_upn):\s*([^\s\"']+@[^\s\"']+)\s*$",
    re.MULTILINE)
# Initials adjacent to the 'JM' self-token via a ':' or '|' separator, in either
# order: 'JA:JM 1:1', 'AG : JM', 'JM|IM', 'IM|JM 1|1'.
_JM_INITIALS_RE = re.compile(
    r"(?:\b([a-z]{2,3})\s*[:|]\s*jm\b)|(?:\bjm\s*[:|]\s*([a-z]{2,3})\b)",
    re.IGNORECASE)


def _build_initials_map(slugs: set[str]) -> dict[str, str]:
    """{initials: slug} for slugs whose initials are unambiguous across the set.

    Initials = first char of each non-stopword token: 'jessica-allen' → 'ja'.
    Only 2–3 char initials are kept (single letters collide wildly); 'jm' is the
    self-token and never a contact. Ambiguous initials are dropped entirely."""
    _STOPWORDS = {"the", "and", "for", "with", "to", "of", "in", "on", "at",
                  "a", "an", "1", "2", "3", "old", "new"}
    by_initials: dict[str, list[str]] = {}
    for slug in slugs:
        tokens = [t for t in slug.split("-") if t and t not in _STOPWORDS]
        initials = "".join(t[0] for t in tokens if t).lower()
        if not (2 <= len(initials) <= 3) or initials == "jm":
            continue
        by_initials.setdefault(initials, []).append(slug)
    resolved = {ini: ss[0] for ini, ss in by_initials.items() if len(ss) == 1}
    # User's authoritative initials-style shorthands win over mechanical ones,
    # e.g. 'lr' means Leeroy Phillips even though Lee Redden's initials are also
    # 'lr'. Overlaying both sets the right target and clears the collision.
    aliases = {"lx": "louisa-xu", "lr": "leeroy-phillips", "hz": "hanzhao"}
    for alias, target in aliases.items():
        if target in slugs:
            resolved[alias] = target
    return resolved


def _build_email_map(files: list[Path]) -> dict[str, str]:
    """{email_lower: slug} from each person file's channels.* email fields."""
    email_map: dict[str, str] = {}
    for path in files:
        slug = _slug_from_filename(path)
        if not slug:
            continue
        try:
            fm, _ = _parse_frontmatter(path.read_text(errors="ignore"))
        except OSError:
            continue
        if not fm:
            continue
        for m in _EMAIL_FM_RE.finditer(fm):
            email_map[m.group(1).strip().lower()] = slug
    return email_map


def _match_event(title: str, attendee_emails, initials_map: dict[str, str],
                 email_map: dict[str, str]) -> set[str]:
    """Pure matcher: return the set of slugs an event resolves to.

    1. attendee email exact match (high precision, any calendar)
    2. '<initials>:JM'/'JM:<initials>' 1:1 title via unambiguous initials map
    """
    hits: set[str] = set()
    for email in attendee_emails or ():
        slug = email_map.get((email or "").strip().lower())
        if slug:
            hits.add(slug)
    for m in _JM_INITIALS_RE.finditer(title or ""):
        ini = (m.group(1) or m.group(2) or "").lower()
        slug = initials_map.get(ini)
        if slug:
            hits.add(slug)
    return hits


def _gcal_service():
    """Build a Calendar API client from the MCP's stored OAuth token, refreshing
    and persisting it. Returns None (silently) if libs or creds are unavailable
    so the daemon degrades gracefully when run offline."""
    import json
    try:
        import warnings
        warnings.filterwarnings("ignore")
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except Exception:
        return None
    if not (_GCAL_OAUTH_KEYS.exists() and _GCAL_TOKENS.exists()):
        return None
    try:
        keys = json.loads(_GCAL_OAUTH_KEYS.read_text())
        k = keys.get("installed") or keys.get("web") or keys
        toks = json.loads(_GCAL_TOKENS.read_text())
        t = toks.get(GCAL_ACCOUNT)
        if not t:
            return None
        creds = Credentials(
            token=t.get("access_token"), refresh_token=t.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=k["client_id"], client_secret=k["client_secret"],
            scopes=(t.get("scope") or "").split())
        if not creds.valid:
            creds.refresh(Request())
            # Persist the refreshed access token so headless runs don't re-refresh.
            try:
                t["access_token"] = creds.token
                if creds.expiry:
                    t["expiry_date"] = int(creds.expiry.timestamp() * 1000)
                toks[GCAL_ACCOUNT] = t
                _GCAL_TOKENS.write_text(json.dumps(toks))
            except Exception:
                pass
        from googleapiclient.discovery import build as _build
        return _build("calendar", "v3", credentials=creds, cache_discovery=False)
    except Exception as exc:
        print(f"warn: gcal auth failed: {exc}", file=sys.stderr)
        return None


def _fetch_calendar_signal(days: int, slugs: set[str],
                           files: list[Path]) -> dict[str, date]:
    """Return {slug: latest_date} from Google Calendar. See module docstring for
    the matching policy. Silently returns {} on any failure."""
    if not slugs:
        return {}
    svc = _gcal_service()
    if svc is None:
        return {}
    initials_map = _build_initials_map(slugs)
    email_map = _build_email_map(files)
    if not initials_map and not email_map:
        return {}

    today = date.today()
    cutoff = today - timedelta(days=days)
    time_min = datetime(cutoff.year, cutoff.month, cutoff.day).isoformat() + "Z"
    time_max = (datetime(today.year, today.month, today.day)
                + timedelta(days=1)).isoformat() + "Z"

    latest: dict[str, date] = {}

    def _event_date(ev) -> date | None:
        s = ev.get("start", {})
        raw = s.get("dateTime") or s.get("date")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return datetime.strptime(raw[:10], "%Y-%m-%d").date()
            except ValueError:
                return None

    for cal_id in GCAL_CAL_IDS:
        page_token = None
        while True:
            try:
                resp = svc.events().list(
                    calendarId=cal_id, timeMin=time_min, timeMax=time_max,
                    singleEvents=True, orderBy="startTime", maxResults=250,
                    pageToken=page_token).execute()
            except Exception as exc:
                print(f"warn: gcal list {cal_id} failed: {exc}", file=sys.stderr)
                break
            for ev in resp.get("items", []):
                d = _event_date(ev)
                if not d or d > today or d < cutoff:
                    continue
                emails = [a.get("email") for a in ev.get("attendees", []) or []]
                for slug in _match_event(ev.get("summary", ""), emails,
                                         initials_map, email_map):
                    if slug not in latest or d > latest[slug]:
                        latest[slug] = d
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
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
    ap.add_argument("--no-calendar", action="store_true",
                    help="Skip the Google Calendar signal (offline/fast runs)")
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
    if args.no_calendar:
        cal_sig = {}
        print("  gcal:  skipped (--no-calendar)")
    else:
        cal_sig = _fetch_calendar_signal(args.days, slugs, files)
        print(f"  gcal:  {len(cal_sig)} slugs with calendar activity")
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
                      "d358": d358_sig.get(slug),
                      "gcal": cal_sig.get(slug)}
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
