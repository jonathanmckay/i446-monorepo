#!/usr/bin/env python3
"""m5x2_weekly_links — file the team's m5x2 weekly updates into f695 as links.

/ibxm5x2 step 1 (2026-10-23). The m5x2 weekly updates (LX, Ian, Andie, Stef,
Leeroy, Florencia, Lawrence, Olga, Jesus, Viviana, Tim) are matched with the SAME sender/subject
rules the m5x2 weekly-reports dash uses (tools/m5x2-weekly-reports/build.py
REPORTERS), so the doc and the dash never disagree about what counts.

Each update becomes one link line in the f695 doc
(~/vault/h335/f693/1-f695-weekly-updates.md) under

    ## m5x2 weekly updates
    ### Week of <Y.MM.DD>          (Sunday-anchored week RECEIVED, like the dash)
    - [LX · Portfolio](<gmail thread>) — m5x2 Weekly Update (10/21)

That section sits after `## Weekly updates`; the dash's i9 parser stops at the
next `## ` heading, so these lines never leak into the i9 grid. Idempotent:
a thread already linked anywhere in the doc is skipped.

With --archive (the skill's default), each linked update still in the inbox
is filed the way ibx files no-response mail: label ai-no-response-needed,
remove INBOX, one line in ~/.config/ibx/agent_filtered.jsonl. Non-destructive
and reversible; never deletes.

Usage: m5x2_weekly_links.py [--days N] [--archive] [--dry-run]
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOC = Path.home() / "vault" / "h335" / "f693" / "1-f695-weekly-updates.md"
SECTION = "## m5x2 weekly updates"
AUDIT_FILE = Path.home() / ".config" / "ibx" / "agent_filtered.jsonl"

_spec = importlib.util.spec_from_file_location(
    "m5x2_weekly_build", HERE.parent / "m5x2-weekly-reports" / "build.py")
wk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wk)


def thread_url(thread: str) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{thread}"


def match(emails: list[dict]) -> list[dict]:
    """Weekly updates only, tagged with reporter + topic (build_grid's rule)."""
    out = []
    for e in emails:
        for disp, topic, _dtok, senders, pat, veto in wk.REPORTERS:
            if e["sender"] in senders and re.search(pat, e["subject"], re.I) \
                    and not (veto and re.search(veto, e["subject"], re.I)):
                out.append({**e, "who": disp, "topic": topic})
                break
    return out


def link_line(u: dict) -> str:
    return (f"- [{u['who']} · {u['topic']}]({thread_url(u['thread'])}) — "
            f"{u['subject']} ({u['date'].month}/{u['date'].day})")


def insert_links(text: str, updates: list[dict]) -> tuple[str, list[dict]]:
    """Add one line per not-yet-linked update under its week heading in the
    m5x2 section (created after `## Weekly updates` if missing). Weeks are
    newest first; lines within a week keep date order. Returns (text, added)."""
    new = [u for u in updates if u["thread"] not in text]
    if not new:
        return text, []
    lines = text.splitlines()
    if SECTION not in lines:
        # After the whole `## Weekly updates` section (before the next `## `),
        # else at the end of the doc.
        start = next((i for i, l in enumerate(lines) if l.strip() == "## Weekly updates"), None)
        at = len(lines)
        if start is not None:
            at = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        block = ["", SECTION, "",
                 "Links to the team's weekly update emails, filed by `/ibxm5x2` under the week received.", ""]
        lines[at:at] = block
    sec = lines.index(SECTION)
    end = next((i for i in range(sec + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = lines[sec + 1:end]

    by_week: dict[dt.date, list[dict]] = {}
    for u in sorted(new, key=lambda u: u["date"]):
        by_week.setdefault(wk.week_start(u["date"]), []).append(u)
    for week in sorted(by_week, reverse=True):
        head = f"### Week of {week:%Y.%m.%d}"
        adds = [link_line(u) for u in by_week[week]]
        if head in body:
            i = body.index(head) + 1
            while i < len(body) and body[i].startswith("- "):
                i += 1
            body[i:i] = adds
        else:
            # Newest-first: before the first older week heading.
            i = next((j for j, l in enumerate(body)
                      if (w := wk._parse_week_heading(l)) is not None and w < week), None)
            chunk = [head] + adds + [""]
            if i is None:
                while body and body[-1] == "":
                    body.pop()
                body += [""] + chunk
            else:
                body[i:i] = chunk
    lines[sec + 1:end] = body
    return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), new


def archive(updates: list[dict], dry_run: bool) -> int:
    """File linked updates still in the inbox: label + remove INBOX (thread)."""
    sys.path.insert(0, str(HERE))
    import ibx
    acct = next(a for a in ibx.ACCOUNTS if a["name"] == "m5c7")
    svc = ibx.get_gmail_service(acct["tokens"], acct["creds"])
    label = ibx.get_or_create_label(svc, ibx.TRIAGE_LABEL)
    done = 0
    for u in updates:
        t = svc.users().threads().get(userId="me", id=u["thread"], format="minimal").execute()
        if not any("INBOX" in (m.get("labelIds") or []) for m in t.get("messages", [])):
            continue
        print(f"  file: {u['who']} · {u['subject'][:60]}")
        if dry_run:
            continue
        svc.users().threads().modify(userId="me", id=u["thread"],
                                     body={"addLabelIds": [label], "removeLabelIds": ["INBOX"]}).execute()
        done += 1
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_FILE, "a") as f:
            f.write(json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"),
                                "source": "m5x2", "id": u["thread"], "from": u["sender"],
                                "subject": u["subject"], "why": "weekly update → f695"}) + "\n")
    return done


def main(argv: list[str]) -> int:
    days = 21
    if "--days" in argv:
        days = int(argv[argv.index("--days") + 1])
    dry = "--dry-run" in argv
    updates = match(wk.fetch_emails(wk.access_token(), days))
    text = DOC.read_text(encoding="utf-8")
    new_text, added = insert_links(text, updates)
    for u in added:
        print(f"  link: {u['who']} · {u['subject'][:60]} ({u['date']:%m/%d})")
    if added and not dry:
        DOC.write_text(new_text, encoding="utf-8")
    filed = archive(updates, dry) if "--archive" in argv else 0
    print(json.dumps({"found": len(updates), "linked": len(added), "filed": filed, "dry_run": dry}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
