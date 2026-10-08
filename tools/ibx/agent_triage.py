#!/usr/bin/env python3
"""
agent_triage — agent pre-filter for the ibx habits, fired when their Toggl
timer starts (hook in mcp/toggl_server/toggl_cli.py cmd_start).

  ibx i9    → Outlook (work) via Agency mail MCP; classified by
              `agency copilot -p`, so work mail stays on Microsoft's agent.
  ibx m5x2  → Gmail m5c7; classified by `claude -p`.

The agent only classifies. It receives the emails as text and answers with
JSON; this script does the filtering, so neither agent can reply, delete or
send (agency copilot -p also auto-denies tool calls). "Filtered" uses the
same non-destructive move as the rest of ibx:
  Outlook → mark read (stays in the inbox; ibx fetches isRead eq false only)
  Gmail   → label ai-no-response-needed, remove INBOX
Every filtered email is appended to ~/.config/ibx/agent_filtered.jsonl so a
bad call can be found and undone.

Usage: agent_triage.py i9|m5x2 [--dry-run]
"""

import fcntl
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

AGENCY = str(Path.home() / ".config/agency/CurrentVersion/agency")
CLAUDE = "/opt/homebrew/bin/claude"
STATE_DIR = Path.home() / ".config" / "ibx"
AUDIT_FILE = STATE_DIR / "agent_filtered.jsonl"
LOCK_FILE = STATE_DIR / "agent_triage.{}.lock"
BODY_CHARS = 800

CRITERIA = """For each email decide whether it needs a response or action from me (Jonathan McKay, the recipient).

"skip" = no response needed: newsletters, marketing, automated status/notification mail, FYI broadcasts and distribution-list announcements, shipping/receipts, social notifications, calendar noise, threads where I am only CC'd and nothing is asked of me.
"keep" = a person asking me a question or for a decision, review, approval or action; scheduling that needs my input; anything addressed to me personally; legal, tax, lease or deadline notices; "Action Required"; anything that should be read, saved or forwarded.
{extra}
When in doubt, keep. Wrongly skipping an email costs far more than keeping one.

Answer with ONLY a JSON array, one object per email, no prose and no code fence:
[{{"i": 1, "v": "keep" or "skip", "why": "<6 words"}}]

EMAILS:
"""

EXTRA = {
    "i9": "Context: this is my Microsoft (Xbox) work inbox. Mail from my manager, skip-level, direct reports or a named teammate is keep unless clearly automated.",
    "m5x2": "Context: this is my real-estate company inbox (McKay Capital / m5x2). Property-management email (tenants, maintenance, inspections, leasing, owners, lenders, introductions) is keep.",
}


def log(msg):
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def build_prompt(source, emails):
    parts = [CRITERIA.format(extra=EXTRA[source])]
    for n, e in enumerate(emails, 1):
        body = re.sub(r"\s+", " ", e["body"] or "")[:BODY_CHARS]
        parts.append(f"--- [{n}]\nFrom: {e['from']}\nSubject: {e['subject']}\n{body}\n")
    return "\n".join(parts)


def parse_verdicts(text, n):
    """Return {index: why} for emails the agent said to skip. Anything
    unparseable or out of range is kept (fail closed = keep)."""
    m = None
    for m in re.finditer(r"\[\s*\{.*?\}\s*\]", text or "", re.DOTALL):
        pass  # last JSON array wins: agents sometimes echo the format first
    if not m:
        return None
    try:
        rows = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    skips = {}
    for r in rows:
        try:
            i = int(r.get("i"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= i <= n and str(r.get("v", "")).lower() == "skip":
            skips[i] = str(r.get("why", ""))[:80]
    return skips


def ask_agency(prompt):
    r = subprocess.run([AGENCY, "copilot", "-p", prompt],
                       capture_output=True, text=True, timeout=300, cwd="/tmp")
    return r.stdout


def ask_claude(prompt):
    # Neutral cwd so no project CLAUDE.md steers the classification.
    r = subprocess.run([CLAUDE, "-p", "--model", "sonnet", prompt],
                       capture_output=True, text=True, timeout=300, cwd="/tmp")
    return r.stdout


# ── Sources ───────────────────────────────────────────────────────────────────

def fetch_i9():
    import outlook_agency
    items = outlook_agency.fetch_outlook_items()  # unread, last 24h, noise-filtered
    return [{"id": it["_data"]["item_id"], "from": it["from"],
             "subject": it["_data"]["email"]["subject"], "body": it["body"]}
            for it in items]


def filter_i9(e):
    import outlook_agency
    outlook_agency._clear_after_action(e["id"].replace("outlook:", "", 1))
    outlook_agency._mark_processed(e["id"])


_gmail = {}


def fetch_m5x2():
    import ibx
    acct = next(a for a in ibx.ACCOUNTS if a["name"] == "m5c7")
    svc = _gmail["svc"] = ibx.get_gmail_service(acct["tokens"], acct["creds"])
    _gmail["label"] = ibx.get_or_create_label(svc, ibx.TRIAGE_LABEL)
    out = []
    for ref in ibx.fetch_inbox(svc, max_results=100, unread_only=True):
        e = ibx.get_email(svc, ref["id"])
        out.append({"id": ref["id"], "from": e["from"], "subject": e["subject"], "body": e["body"]})
    return out


def filter_m5x2(e):
    _gmail["svc"].users().messages().modify(
        userId="me", id=e["id"],
        body={"addLabelIds": [_gmail["label"]], "removeLabelIds": ["INBOX"]},
    ).execute()


SOURCES = {
    "i9": (fetch_i9, ask_agency, filter_i9),
    "m5x2": (fetch_m5x2, ask_claude, filter_m5x2),
}


def notify(title, msg):
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "{title}"'],
                   capture_output=True, timeout=10)


def run(source, dry_run=False):
    fetch, ask, do_filter = SOURCES[source]
    emails = fetch()
    log(f"{source}: {len(emails)} unread")
    if not emails:
        return 0, 0
    skips = parse_verdicts(ask(build_prompt(source, emails)), len(emails))
    if skips is None:
        log(f"{source}: agent returned no parseable verdicts; filtering nothing")
        return len(emails), 0
    done = 0
    for i, why in sorted(skips.items()):
        e = emails[i - 1]
        log(f"  skip: {e['subject'][:70]}  ({why})")
        if dry_run:
            continue
        try:
            do_filter(e)
        except Exception as ex:
            log(f"  filter failed: {ex}")
            continue
        done += 1
        with open(AUDIT_FILE, "a") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                                "source": source, "id": e["id"], "from": e["from"],
                                "subject": e["subject"], "why": why}) + "\n")
    return len(emails), done


def main():
    args = sys.argv[1:]
    if not args or args[0] not in SOURCES:
        sys.exit("Usage: agent_triage.py i9|m5x2 [--dry-run]")
    source, dry = args[0], "--dry-run" in args
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # One run per source at a time: restarting the timer mid-triage is a no-op.
    lock = open(str(LOCK_FILE).format(source), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log(f"{source}: triage already running")
        return
    try:
        total, filtered = run(source, dry)
    except Exception as ex:
        log(f"{source}: triage failed: {ex}")
        notify(f"ibx {source}", "agent triage failed, see agent_triage.log")
        return
    if total:
        notify(f"ibx {source}", f"agent filtered {filtered} of {total}; {total - filtered} need you")


if __name__ == "__main__":
    main()
