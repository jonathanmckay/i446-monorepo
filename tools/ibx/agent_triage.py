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

Usage: agent_triage.py i9|m5x2 [--dry-run] [--all] [--days N] [--recheck]
       agent_triage.py cost [--days N]
  --all      m5x2: classify read inbox mail too, not just unread
  --days N   m5x2: how far back to look (default 30)
  --recheck  ask the agent about every email again, ignoring the keep cache
  cost       what the agent calls cost, per day and source (default 30 days)

An email the agent said to keep is remembered in agent_kept.json and not sent
again (2026-11-24: 69 emails went to claude to file 9, and the 60 kept ones
were paid for again on every run). The cache is dropped whenever the criteria
text changes. Every agent call appends one row to agent_cost.jsonl.

Mail is classified BATCH emails per agent call and each batch is filed as soon
as its verdicts arrive (2026-11-15: one 100-email prompt filed 15 and left a
day of obvious newsletters; a single call also lost everything on a timeout).
"""

import fcntl
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

AGENCY = str(Path.home() / ".config/agency/CurrentVersion/agency")
CLAUDE = "/opt/homebrew/bin/claude"
STATE_DIR = Path.home() / ".config" / "ibx"
AUDIT_FILE = STATE_DIR / "agent_filtered.jsonl"
LOCK_FILE = STATE_DIR / "agent_triage.{}.lock"
KEEP_FILE = STATE_DIR / "agent_kept.json"    # {source: {"key": criteria hash, "ids": {id: {why, ts}}}}
COST_FILE = STATE_DIR / "agent_cost.jsonl"   # one row per agent call
KEEP_DAYS = 45       # cached keeps older than this are dropped (window is 30)
BODY_CHARS = 800
BATCH = 20           # emails per agent call
DEFAULT_DAYS = 30    # m5x2 look-back window

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
    "m5x2": "Context: this is my real-estate company inbox (McKay Capital / m5x2). Property-management email (tenants, maintenance, inspections, leasing, owners, lenders, investors, introductions) is keep, including threads where my team is replying, unless the thread is plainly closed. Hiring is keep too: job applications (including automated applicant notices), recruiter replies and any thread I started about a role I am filling. Weekly updates from my team are keep.",
}


# Senders filed without asking the agent: (regex on From, reason). JM
# 2026-11-15: "File all Property Meld". Its merge/status notifications (79 in
# one evening) were kept by the maintenance-is-keep rule above.
RULE_SKIP = {
    "m5x2": [(re.compile(r"@msg\.propertymeld\.com\b", re.I), "Property Meld notification (rule)")],
}


def rule_skip(source, e):
    """Reason string when a fixed sender rule files this email, else None."""
    for rx, why in RULE_SKIP.get(source, []):
        if rx.search(e.get("from") or ""):
            return why
    return None


def filed_before(source):
    """Ids this source's triage has already filed. One of them showing up in
    the inbox again means a person put it back, so it is never re-filed
    (2026-11-15: a restored recruiter reply was filed again on the next run)."""
    ids = set()
    try:
        with open(AUDIT_FILE) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("source") == source:
                    ids.add(r.get("id"))
    except OSError:
        pass
    return ids


def criteria_key(source):
    """Changes whenever the instructions the agent sees change, so an edited
    rule re-judges mail kept under the old one."""
    return hashlib.sha1((CRITERIA + EXTRA[source]).encode()).hexdigest()[:12]


def load_kept(source):
    """{id: {"why", "ts"}} the agent already said to keep under the current
    criteria, minus entries past KEEP_DAYS. Unreadable file = empty."""
    try:
        d = json.loads(KEEP_FILE.read_text()).get(source) or {}
    except (OSError, ValueError, AttributeError):
        return {}
    if d.get("key") != criteria_key(source):
        return {}
    floor = (datetime.now() - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
    return {i: v for i, v in (d.get("ids") or {}).items() if (v.get("ts") or "") >= floor}


def save_kept(source, kept):
    try:
        allk = json.loads(KEEP_FILE.read_text())
    except (OSError, ValueError):
        allk = {}
    allk[source] = {"key": criteria_key(source), "ids": kept}
    tmp = KEEP_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(allk, ensure_ascii=False))
    tmp.replace(KEEP_FILE)


def log_cost(source, n_emails):
    """One ledger row for the agent call just made. cost_usd is what `claude
    -p` reports (API-rate price of the call); agency reports nothing, so its
    rows carry the call and email count only."""
    u = _usage.pop() if _usage else {}
    _usage.clear()
    try:
        with open(COST_FILE, "a") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                                "source": source, "emails": n_emails,
                                "cost_usd": u.get("cost_usd"), "in": u.get("in"),
                                "out": u.get("out")}) + "\n")
    except OSError:
        pass
    return u.get("cost_usd") or 0


def cost_report(days=30):
    """Lines: per day and source, calls / emails / dollars, then a total."""
    floor = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    agg = {}
    try:
        with open(COST_FILE) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if (r.get("ts") or "") < floor:
                    continue
                a = agg.setdefault((r["ts"][:10], r.get("source", "?")), [0, 0, 0.0])
                a[0] += 1
                a[1] += r.get("emails") or 0
                a[2] += r.get("cost_usd") or 0
    except OSError:
        pass
    out = [f"{d}  {s:5} {c:3} calls {e:4} emails  ${usd:.2f}" for (d, s), (c, e, usd) in sorted(agg.items())]
    tot = [sum(v[i] for v in agg.values()) for i in range(3)]
    out.append(f"last {days}d: {tot[0]} calls, {tot[1]} emails, ${tot[2]:.2f}")
    return out


def log(msg):
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def build_prompt(source, emails):
    parts = [CRITERIA.format(extra=EXTRA[source])]
    for n, e in enumerate(emails, 1):
        body = re.sub(r"\s+", " ", e["body"] or "")[:BODY_CHARS]
        parts.append(f"--- [{n}]\nFrom: {e['from']}\nSubject: {e['subject']}\n{body}\n")
    return "\n".join(parts)


def parse_rows(text, n):
    """Return {index: (verdict, why)} for every in-range row the agent gave,
    or None when no JSON array parses."""
    m = None
    for m in re.finditer(r"\[\s*\{.*?\}\s*\]", text or "", re.DOTALL):
        pass  # last JSON array wins: agents sometimes echo the format first
    if not m:
        return None
    try:
        rows = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    out = {}
    for r in rows:
        try:
            i = int(r.get("i"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= i <= n:
            out[i] = (str(r.get("v", "")).lower(), str(r.get("why", ""))[:80])
    return out


def parse_verdicts(text, n):
    """Return {index: why} for emails the agent said to skip. Anything
    unparseable or out of range is kept (fail closed = keep)."""
    rows = parse_rows(text, n)
    if rows is None:
        return None
    return {i: why for i, (v, why) in rows.items() if v == "skip"}


def ask_agency(prompt):
    # --no-custom-instructions: the global instructions' terminal-color ritual
    # otherwise eats the turn and the answer never prints. --available-tools
    # with no names = no tools at all: classification only, no mail access.
    r = subprocess.run([AGENCY, "copilot", "-p", prompt, "--",
                        "-s", "--no-custom-instructions", "--available-tools"],
                       capture_output=True, text=True, timeout=300, cwd="/tmp")
    return r.stdout


_usage = []   # what the last ask_* call cost; run() moves it to the ledger


def ask_claude(prompt):
    # Neutral cwd so no project CLAUDE.md steers the classification.
    # JSON output carries the answer plus the call's cost and token counts.
    r = subprocess.run([CLAUDE, "-p", "--model", "sonnet", "--output-format", "json", prompt],
                       capture_output=True, text=True, timeout=300, cwd="/tmp")
    try:
        d = json.loads(r.stdout)
        text = d["result"]
    except (ValueError, KeyError, TypeError):
        return r.stdout   # not the JSON envelope: let the parser see it raw
    u = d.get("usage") or {}
    _usage.append({"cost_usd": d.get("total_cost_usd"),
                   "in": sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                     "cache_read_input_tokens")),
                   "out": u.get("output_tokens") or 0})
    return text


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
    # The timer hook triages unread only; /ibxm5x2 passes --all (2026-10-23)
    # so read-but-unanswered inbox mail gets the same pass. The whole window
    # is paged through (2026-11-15: a 100-message cap hid older mail).
    q = f"in:inbox newer_than:{_gmail.get('days', DEFAULT_DAYS)}d"
    if not _gmail.get("all"):
        q += " is:unread"
    refs, token = [], None
    while True:
        r = svc.users().messages().list(userId="me", q=q, maxResults=500, pageToken=token).execute()
        refs += r.get("messages", [])
        token = r.get("nextPageToken")
        if not token:
            break
    for ref in refs:
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


def run(source, dry_run=False, recheck=False):
    fetch, ask, do_filter = SOURCES[source]
    emails = fetch()
    log(f"{source}: {len(emails)} {'in inbox' if _gmail.get('all') else 'unread'}")
    if not emails:
        return 0, 0
    done = 0

    def file_it(e, why):
        log(f"  skip: {e['subject'][:70]}  ({why})")
        if dry_run:
            return 0
        try:
            do_filter(e)
        except Exception as ex:
            log(f"  filter failed: {ex}")
            return 0
        with open(AUDIT_FILE, "a") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                                "source": source, "id": e["id"], "from": e["from"],
                                "subject": e["subject"], "why": why}) + "\n")
        return 1

    total, restored = len(emails), filed_before(source)
    back = [e for e in emails if e["id"] in restored]
    for e in back:
        log(f"  keep: {e['subject'][:70]}  (restored after an earlier filing)")
    emails = [e for e in emails if e["id"] not in restored]
    ruled = [(e, rule_skip(source, e)) for e in emails]
    for e, why in ruled:
        if why:
            done += file_it(e, why)
    emails = [e for e, why in ruled if not why]
    if len(ruled) != len(emails):
        log(f"  rules: {len(ruled) - len(emails)} filed by sender rule, {len(emails)} to the agent")
    # Mail the agent already said to keep is not asked about again.
    kept = {} if recheck else load_kept(source)
    cached = [e for e in emails if e["id"] in kept]
    for e in cached:
        log(f"  keep: {e['subject'][:70]}  ({kept[e['id']].get('why', '')}; cached)")
    emails = [e for e in emails if e["id"] not in kept]
    if cached:
        log(f"  cache: {len(cached)} kept earlier, {len(emails)} new to the agent")
    spent, now = 0.0, datetime.now().isoformat(timespec="seconds")
    for start in range(0, len(emails), BATCH):
        batch = emails[start:start + BATCH]
        label = f"batch {start // BATCH + 1}"
        # One batch failing (timeout, garbage) keeps that batch and moves on.
        try:
            answer = ask(build_prompt(source, batch))
        except Exception as ex:
            log(f"  {label}: agent call failed ({ex}); keeping {len(batch)}")
            continue
        spent += log_cost(source, len(batch))
        rows = parse_rows(answer, len(batch))
        if rows is None:
            log(f"  {label}: no parseable verdicts; keeping {len(batch)}")
            continue
        skips = {i: why for i, (v, why) in rows.items() if v == "skip"}
        log(f"  {label}: sent {len(batch)}, verdicts {len(rows)}, skip {len(skips)}, "
            f"keep {len(batch) - len(skips)}")
        for i, (v, why) in sorted(rows.items()):
            e = batch[i - 1]
            if v != "skip":
                log(f"  keep: {e['subject'][:70]}  ({why})")
                kept[e["id"]] = {"why": why, "ts": now}
                continue
            done += file_it(e, why)
        if not dry_run:
            save_kept(source, kept)   # per batch: a later crash loses nothing
    log(f"{source}: filed {done} of {total}" + (f"; agent cost ${spent:.2f}" if spent else ""))
    return total, done


def main():
    args = sys.argv[1:]
    if args and args[0] == "cost":
        try:
            days = int(args[args.index("--days") + 1]) if "--days" in args else 30
        except (IndexError, ValueError):
            sys.exit("--days needs a whole number of days")
        print("\n".join(cost_report(days)))
        return
    if not args or args[0] not in SOURCES:
        sys.exit("Usage: agent_triage.py i9|m5x2 [--dry-run] [--all] [--days N] [--recheck]\n"
                 "       agent_triage.py cost [--days N]")
    source, dry = args[0], "--dry-run" in args
    _gmail["all"] = "--all" in args
    if "--days" in args:
        try:
            _gmail["days"] = int(args[args.index("--days") + 1])
        except (IndexError, ValueError):
            sys.exit("--days needs a whole number of days")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # One run per source at a time: restarting the timer mid-triage is a no-op.
    lock = open(str(LOCK_FILE).format(source), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log(f"{source}: triage already running")
        return
    try:
        total, filtered = run(source, dry, recheck="--recheck" in args)
    except Exception as ex:
        log(f"{source}: triage failed: {ex}")
        notify(f"ibx {source}", "agent triage failed, see agent_triage.log")
        return
    if total:
        notify(f"ibx {source}", f"agent filtered {filtered} of {total}; {total - filtered} need you")


if __name__ == "__main__":
    main()
