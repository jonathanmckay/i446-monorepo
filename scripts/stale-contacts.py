#!/usr/bin/env python3
"""stale-contacts.py — scan d359 contacts and create Todoist outreach tasks for
anyone past their cadence. Auto-generated tasks are prefixed with 😈 so they're
distinguishable from tasks JM creates by hand.

This is the engine behind /stale-contacts and the daily launchd job. A contact
is overdue when (today - last_contact) exceeds the cadence threshold. Each d359
file may set `outreach_task` to override the default "Reach out to <Name>" with
a custom task body (e.g. "call mom (20) [20]" for parents).

Usage:
    stale-contacts.py            # dry-run: list who's overdue + what it would create
    stale-contacts.py --apply    # create the Todoist tasks
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

D359 = Path.home() / "vault/d359"
REFRESH = Path.home() / "i446-monorepo/tools/d359/refresh_last_contact.py"
API = "https://api.todoist.com/api/v1"
AUTO_MARK = "😈"  # prefixes every auto-generated task
DEFAULT_POINTS = 10  # [N] value on the default "Reach out to <name>" body

# Days since last_contact before a contact is flagged overdue.
THRESHOLDS = {
    "weekly": 10, "biweekly": 17, "monthly": 38, "quarterly": 100,
    "semi-annual": 200, "semiannual": 200, "annual": 400, "yearly": 400,
    "biennial": 800,
}
# Spellings seen in d359 that mean a named cadence above.
CADENCE_ALIASES = {
    "6 months": "semi-annual", "six months": "semi-annual", "6-months": "semi-annual",
    "every 6 months": "semi-annual", "bi-annual": "semi-annual", "biannual": "semi-annual",
    "fortnightly": "biweekly", "bi-weekly": "biweekly", "every 2 weeks": "biweekly",
    "every-2-weeks": "biweekly", "every year": "annual", "every 2 years": "biennial",
    "1 year": "annual", "12 months": "annual", "3 months": "quarterly",
    "1 month": "monthly", "1 week": "weekly", "2 weeks": "biweekly",
    "24 months": "biennial", "2 years": "biennial",
}
# Cadences that mean "no scheduled outreach": silently not tracked.
NO_CADENCE = {"none", "as-needed", "as needed", "ad-hoc", "adhoc", "ad hoc", "never", "n/a", ""}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
_GENERIC = re.compile(r"^(?:every[\s-]+)?(\d+)[\s-]*(day|week|month|year)s?$")


def cadence_threshold(raw: str | None) -> int | None:
    """Days-since-last_contact threshold for a cadence string.

    Returns None for "no cadence" values (none/as-needed/ad-hoc/blank).
    Raises ValueError for strings it cannot interpret, so callers report them
    instead of silently dropping the contact. Generic forms like
    `every-5-days`, `3 weeks`, `every 2 months` get ~10% grace (min 3 days),
    matching the slack baked into the named thresholds."""
    c = (raw or "").split("#", 1)[0].strip().strip('"').strip("'").lower()
    c = re.sub(r"\s+", " ", c)
    if c in NO_CADENCE:
        return None
    c = CADENCE_ALIASES.get(c, c)
    if c in THRESHOLDS:
        return THRESHOLDS[c]
    m = _GENERIC.match(c)
    if m:
        period = int(m.group(1)) * _UNIT_DAYS[m.group(2)]
        if period <= 0:
            raise ValueError(f"non-positive cadence: {raw!r}")
        return period + max(3, -(-period // 10))
    raise ValueError(f"unknown cadence: {raw!r}")


SKIP_FILES = {"CLAUDE.md", "d359-index.md"}


def parse_frontmatter(text: str) -> dict:
    """Minimal flat-YAML frontmatter parse (key: value). Good enough for the
    scalar fields we read; avoids a hard pyyaml dependency."""
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).split("\n"):
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def slug_of(path: Path) -> str:
    return re.sub(r"[ -]d359$", "", path.stem).strip().rstrip("-")


# Tags that are real outreach-time rollup domains. A d359 file carrying one of
# these in `tags:` routes its outreach task/points there instead of the s897
# default. Everything else (h335 work contacts, junk tags) stays on s897, which
# is where person-outreach has always rolled up. Extend this set to divert more
# domains (e.g. add "i9"/"m5x2" to bucket work-contact outreach as work time).
ROLLUP_DOMAINS = {"家", "xk87", "xk88"}


def rollup_label(fm: dict) -> str:
    """The domain label a contact's outreach time/points roll up to. A whitelisted
    domain tag (e.g. 家 for family) wins; otherwise s897 (social), the historical
    catch-all for reaching out to people."""
    raw = fm.get("tags", "")
    tags = {t.strip() for t in raw.strip("[]").split(",") if t.strip()}
    hits = tags & ROLLUP_DOMAINS
    return next(iter(hits)) if hits else "s897"


def contact_files(d359_dir: Path = D359) -> list[Path]:
    """Every person doc under d359, recursing into subfolders. In a subfolder
    only the folder note (`louisa-xu-d359/louisa-xu-d359.md`) is a person doc;
    siblings are meeting notes/PSCs and are ignored."""
    out = []
    for p in sorted(d359_dir.rglob("*.md")):
        rel = p.relative_to(d359_dir)
        if p.name in SKIP_FILES or any(part.startswith(".") for part in rel.parts):
            continue
        if len(rel.parts) > 1 and p.stem != p.parent.name:
            continue
        out.append(p)
    return out


def scan(today: date, d359_dir: Path = D359) -> tuple[list[dict], list[dict]]:
    """Scan d359 person docs. Returns (overdue, problems).

    problems lists docs that carry a cadence but cannot be evaluated (missing
    or malformed last_contact, unparseable cadence) so they are reported
    rather than silently skipped."""
    overdue, problems = [], []
    for p in contact_files(d359_dir):
        fm = parse_frontmatter(p.read_text(errors="replace"))
        rel = str(p.relative_to(d359_dir))
        raw = fm.get("cadence")
        try:
            threshold = cadence_threshold(raw)
        except ValueError:
            problems.append({"file": rel, "issue": "unknown cadence", "value": raw})
            continue
        if threshold is None:
            continue
        lc = (fm.get("last_contact") or "").split("#", 1)[0].strip()
        if lc.lower() in ("", "null", "none", "~", "yyyy-mm-dd"):
            problems.append({"file": rel, "issue": "no last_contact", "value": raw})
            continue
        try:
            lc_date = datetime.strptime(lc[:10], "%Y-%m-%d").date()
        except ValueError:
            problems.append({"file": rel, "issue": "bad last_contact", "value": lc})
            continue
        days = (today - lc_date).days
        if days <= threshold:
            continue  # not overdue (or future-dated)
        slug = slug_of(p)
        name = fm.get("title", slug).replace(" d359", "").strip()
        overdue.append({
            "slug": slug, "name": name,
            "cadence": raw.split("#", 1)[0].strip().lower(),
            "last_contact": lc_date.isoformat(), "days": days, "threshold": threshold,
            "outreach_task": fm.get("outreach_task"),
            "rollup": rollup_label(fm), "file": rel,
        })
    return overdue, problems


def overdue_contacts(today: date, d359_dir: Path = D359) -> list[dict]:
    """Overdue contacts only (see scan() for the problems list)."""
    return scan(today, d359_dir)[0]


def task_content(c: dict) -> str:
    """Build the task body. Custom `outreach_task` wins; else the default."""
    if c.get("outreach_task"):
        body = c["outreach_task"]  # custom body sets its own points; left as-is
    else:
        body = (f"Reach out to {c['name']} (overdue {c['cadence']}: "
                f"last contact {c['last_contact']}) [{DEFAULT_POINTS}]")
    return f"{AUTO_MARK} {body}"


# ------- I/O -------

def _token() -> str:
    import os
    tok = os.environ.get("TODOIST_API_KEY")
    if tok:
        return tok
    cfg = json.loads((Path.home() / ".claude.json").read_text())
    return cfg["mcpServers"]["todoist"]["headers"]["Authorization"].split(None, 1)[1].strip()


def _open_label_set(token: str, rollups: set[str]) -> set[str]:
    """Return the set of d359/<slug> labels that already have an open task, so
    we never create a second outreach task for a contact already queued. Scans
    across every rollup domain in play (e.g. s897, 家) so contacts that roll up
    to 家 still dedupe correctly."""
    labels = set()
    query = " | ".join(f"@{r}" for r in sorted(rollups)) or "@s897"
    q = urllib.parse.quote(query)
    url = f"{API}/tasks/filter?query={q}&limit=200"
    while url:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        d = json.load(urllib.request.urlopen(req))
        for t in d.get("results", []):
            for lab in t.get("labels", []):
                if lab.startswith("d359/"):
                    labels.add(lab)
        c = d.get("next_cursor")
        url = f"{API}/tasks/filter?query={q}&limit=200&cursor={urllib.parse.quote(c)}" if c else None
    return labels


def _create(token: str, content: str, slug: str, rollup: str) -> None:
    body = {"content": content, "labels": [rollup, f"d359/{slug}"],
            "priority": 2, "due_string": "today"}
    req = urllib.request.Request(
        f"{API}/tasks", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    urllib.request.urlopen(req)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="create the tasks (default: dry-run)")
    ap.add_argument("--no-refresh", action="store_true", help="skip the last_contact refresh")
    args = ap.parse_args()

    # Refresh last_contact from Toggl/d358 so stale manual values don't false-positive.
    if not args.no_refresh and REFRESH.exists():
        try:
            subprocess.run([sys.executable, str(REFRESH), "--days", "90", "--apply"],
                           capture_output=True, timeout=120)
        except Exception:
            pass

    today = date.today()
    overdue, problems = scan(today)
    token = _token()
    rollups = {c["rollup"] for c in overdue} | {"s897"}
    already = _open_label_set(token, rollups)

    created, skipped = [], []
    for c in overdue:
        if f"d359/{c['slug']}" in already:
            skipped.append(c["name"])
            continue
        content = task_content(c)
        if args.apply:
            _create(token, content, c["slug"], c["rollup"])
        created.append({"name": c["name"], "content": content, "days": c["days"]})

    print(f"{'CREATED' if args.apply else 'WOULD CREATE'} {len(created)} | skipped {len(skipped)} (already queued)")
    for c in created:
        print(f"  {c['content']}   [{c['days']}d overdue]")
    if problems:
        print(f"NOT EVALUATED {len(problems)} (cadence set but unusable):")
        for pr in problems:
            print(f"  {pr['file']}: {pr['issue']} ({pr['value']!r})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
