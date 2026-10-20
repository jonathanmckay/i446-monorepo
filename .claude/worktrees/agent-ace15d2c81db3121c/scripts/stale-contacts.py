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
}
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


def overdue_contacts(today: date, d359_dir: Path = D359) -> list[dict]:
    """Pure-ish: scan d359 files, return overdue contacts as dicts with the
    fields needed to build a task. Skips files missing cadence/last_contact."""
    out = []
    for p in sorted(d359_dir.glob("*.md")):
        if p.name in SKIP_FILES:
            continue
        fm = parse_frontmatter(p.read_text())
        cadence = (fm.get("cadence") or "").lower()
        lc = fm.get("last_contact")
        if not cadence or not lc or cadence not in THRESHOLDS:
            continue
        try:
            lc_date = datetime.strptime(lc, "%Y-%m-%d").date()
        except ValueError:
            continue
        days = (today - lc_date).days
        if days <= THRESHOLDS[cadence]:
            continue  # not overdue (or future-dated)
        slug = slug_of(p)
        name = fm.get("title", slug).replace(" d359", "").strip()
        out.append({
            "slug": slug, "name": name, "cadence": cadence,
            "last_contact": lc, "days": days,
            "outreach_task": fm.get("outreach_task"),
            "rollup": rollup_label(fm),
        })
    return out


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
    overdue = overdue_contacts(today)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
