#!/usr/bin/env python3
"""picks — shared state for the agent-picks pilot (2026-11-08).

Claude picks <=3 of JM's own priced Todoist tasks a day, finishes them
headlessly and attaches a proof receipt; JM reviews in dtd. His ⌥⏎ close is
the accept, alt-n is the reject. Successor to Dream, which failed because it
chose its own work, delivered proposals instead of finished work, and made
claims nobody could check (vault/g245 2026-11-08 discussion).

Store: ~/vault/z_ibx/agent-picks/ (Syncthing carries it to Straylight)
  events.jsonl            one JSON object per event:
                          pick | abstain | fail | ready | viewed | accept | reject | merged | merge_failed
  receipts/<task_id>/     receipt.md + artifacts; `status` = ready|accepted|rejected|failed
  receipts/<task_id>/branch   agent/<task_id> when the work is a code change

Rules the pilot lives by:
  - only JM's existing tasks with a [N]; agents never create tasks or set [N]
  - no receipt, no ✅: a receipt must carry the required sections + >=1 artifact
  - a class drops out when its accept rate < 70% or its median review time
    > (N)/3 minutes (>= 3 decided outcomes)
"""
from __future__ import annotations

import json
import re
import statistics
import subprocess
import time
from pathlib import Path

ROOT = Path.home() / "vault" / "z_ibx" / "agent-picks"
EVENTS = ROOT / "events.jsonl"
RECEIPTS = ROOT / "receipts"
ALERTS = Path.home() / "vault" / "z_ibx" / "alerts.jsonl"
REPO = Path.home() / "i446-monorepo"
# Repos the worker may change code in (2026-11-09: the AppFolio dashboards
# joined; the Tours Scheduled fix lives there). The worker gets one detached
# worktree per repo, off `base`, and pushes agent/<task_id> to that repo. On
# accept, `land` says how merge.py lands it: "merge" pushes a merge commit to
# base (monorepo wip, like autopush); "pr" opens a GitHub PR (the dashboards
# team lands main by PR and CI deploys it; main is NOT branch-protected on
# GitHub, so the worker's allowlist is the only thing stopping a direct push).
# The receipt's `branch` file lists "<repo> agent/<task_id>" lines; a bare
# "agent/<task_id>" line is monorepo.
CODE_CLASSES = ("code", "dashboard")   # classes that work in worktrees and must ship a branch
REPOS: dict[str, dict] = {
    "monorepo": {"path": REPO, "base": "wip", "land": "merge",
                 "desc": "~/i446-monorepo (JM's tools, vault scripts, dtd, Neon)"},
    "dashboards": {"path": Path.home() / "code" / "appfolio-dashboards", "base": "main", "land": "pr",
                   "desc": "m5x2/appfolio-dashboards (the AppFolio dashboards site: leasing funnel, "
                           "leasing goals, occupancy, grounds, DQ; Python builders in scripts/, "
                           "HTML templates in templates/, deployed by GitHub Actions)"},
}

MAX_PICKS = 3
MIN_CONFIDENCE = 0.7
MIN_DECIDED = 3
MIN_ACCEPT_RATE = 0.7
REVIEW_SHARE = 1 / 3          # review may cost at most this share of the task's (N)
RETRY_AFTER_DAYS = 7          # an abstained/failed task is not re-picked for this long

# Asks (2026-11-09, second pass): a near-miss the picker would take if JM
# supplied one fact gets a `😈? <question>` comment and a parked (released)
# claim so dtd shows it at the top with 🕐; JM answers in the ctrl-o notes
# field (the description) or as a plain comment. The next run reads the
# answer and picks with it in hand.
MAX_ASKS = 3                  # questions posted per run
ASK_RETRY_DAYS = 7            # a task asked or answered inside this window is not asked again
ASK_SESSION = "pick-ask"      # the agent_claims session that owns parked asks
ASK_PREFIX = "😈? "
AGENT_COMMENT_PREFIXES = ("😈", "dtd-short:")   # comments that are never JM's answer

CLASSES = ("code", "dashboard", "analysis", "draft")

# Proof contract per class: what the receipt must show, in the agent's prompt
# and in validate_receipt().
CONTRACT = {
    "code": "a diff (artifact diff.patch), the test command and its output, pushed to branch agent/<task_id>",
    "dashboard": "before and after screenshots or query output, plus the query that reconciles the numbers",
    "analysis": "every claim quoted with its source (file:line, sheet!cell, or URL); no paraphrased claims",
    "draft": "the draft saved as a Gmail/Slack draft or a file in the receipt dir, never sent",
}
SECTIONS = ("## Summary", "## Proof", "## How to verify", "## Not done")

# Labels whose tasks are rituals/goals, not delegable work.
SKIP_LABELS = {"0neon", "1neon", "2neon", "夜neon", "-1g", "0g", "-1neon", "5^2₦",
               "i9",       # Microsoft sources live on Straylight; Ix can't reach them
               "agent"}    # already has a receipt
# Titles that need JM himself: a call, a meeting, something sent under his name, judgment.
SKIP_TITLE = re.compile(
    r"\b(call|meet|meeting|send|reach out|intro|intros|understand|talk|ask|discuss|1:1|"
    r"interview|decide|book|pay|sign)\b|😈", re.I)

_TIME = re.compile(r"\((\d+)\)")
_VAL = re.compile(r"\[(\d+)\]")


# --- events ----------------------------------------------------------------
def log(event: str, task_id: str = "", **kw) -> dict:
    rec = {"ts": int(time.time()), "event": event, "task_id": str(task_id), **kw}
    EVENTS.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def events(path: Path | None = None) -> list[dict]:
    try:
        lines = (path or EVENTS).read_text().splitlines()
    except OSError:
        return []
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except ValueError:
            pass
    return out


def alert(reason: str, detail: str, severity: str = "critical") -> None:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tool": "agent-pick",
           "reason": reason, "severity": severity, "detail": detail}
    try:
        with ALERTS.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


# --- receipts --------------------------------------------------------------
def receipt_dir(task_id: str, root: Path | None = None) -> Path:
    return (root or RECEIPTS) / str(task_id)


def status(task_id: str, root: Path | None = None) -> str | None:
    try:
        return (receipt_dir(task_id, root) / "status").read_text().strip() or None
    except OSError:
        return None


def set_status(task_id: str, st: str, root: Path | None = None) -> None:
    d = receipt_dir(task_id, root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "status").write_text(st + "\n")


def branch(task_id: str, root: Path | None = None) -> str | None:
    try:
        return (receipt_dir(task_id, root) / "branch").read_text().strip() or None
    except OSError:
        return None


def branches(task_id: str, root: Path | None = None) -> list[tuple[str, str]]:
    """[(repo key, branch)] from the receipt's branch file. Each line is
    "<repo> <branch>" or a bare "<branch>" (monorepo, the 11-08 format).
    Unknown repo keys are dropped; duplicates collapse."""
    text = branch(task_id, root) or ""
    alias = {"dashboard": "dashboards", "i446-monorepo": "monorepo", "i446": "monorepo"}
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        repo, br = (parts[0], parts[1]) if len(parts) >= 2 else ("monorepo", parts[0])
        repo = alias.get(repo, repo)
        if repo in REPOS and (repo, br) not in out:
            out.append((repo, br))
    return out


def validate_receipt(task_id: str, cls: str, root: Path | None = None) -> tuple[bool, str]:
    d = receipt_dir(task_id, root)
    try:
        text = (d / "receipt.md").read_text()
    except OSError:
        return False, "no receipt.md"
    missing = [s for s in SECTIONS if s not in text]
    if missing:
        return False, "missing " + ", ".join(missing)
    artifacts = [p for p in d.iterdir()
                 if p.is_file() and p.name not in ("receipt.md", "status", "branch", "agent.log")]
    if not artifacts:
        return False, "no artifact besides receipt.md"
    if cls in CODE_CLASSES:
        if not (d / "diff.patch").exists():
            return False, f"{cls} receipt needs diff.patch"
        if not branches(task_id, root):
            # a branch file naming no known repo would otherwise pass here and
            # then never land (merge.py would find nothing to do)
            return False, (f"{cls} receipt needs a branch file with '<repo> agent/<id>' lines, "
                           f"repo in {', '.join(REPOS)}")
    return True, "ok"


def summary_comment(task_id: str, root: Path | None = None, limit: int = 1500) -> str:
    """The receipt's Summary + How to verify, for the Todoist comment dtd shows on ctrl-o."""
    text = (receipt_dir(task_id, root) / "receipt.md").read_text()
    keep = []
    for sec in ("## Summary", "## How to verify", "## Not done"):
        i = text.find(sec)
        if i < 0:
            continue
        j = text.find("\n## ", i + len(sec))
        keep.append(text[i:j if j > 0 else None].strip())
    body = "\n\n".join(keep)
    if len(body) > limit:
        body = body[:limit].rstrip() + " …"
    path = receipt_dir(task_id, root) / "receipt.md"
    rel = str(path).replace(str(Path.home()), "~")
    return f"😈 receipt — review, then ⌥⏎ accept / alt-n reject\n\n{body}\n\nFull receipt: {rel}"


# --- task facts --------------------------------------------------------------
def minutes_and_value(content: str) -> tuple[int | None, int | None]:
    t, v = _TIME.search(content or ""), _VAL.search(content or "")
    return (int(t.group(1)) if t else None, int(v.group(1)) if v else None)


def eligible(task: dict, claimed: set[str], recent: set[str]) -> tuple[bool, str]:
    tid = str(task.get("id"))
    content = task.get("content") or ""
    if tid in claimed:
        return False, "claimed"
    if tid in recent:
        return False, "attempted recently"
    if (task.get("due") or {}).get("is_recurring") or task.get("recurring"):
        return False, "recurring"
    labels = set(task.get("labels") or [])
    if labels & SKIP_LABELS:
        return False, "label " + ",".join(sorted(labels & SKIP_LABELS))
    if SKIP_TITLE.search(content):
        return False, "needs JM"
    _, val = minutes_and_value(content)
    if not val:
        return False, "no [N]"
    return True, "ok"


def recently_attempted(evs: list[dict], now: float | None = None) -> set[str]:
    """Tasks with any receipt state, or abstained/failed inside RETRY_AFTER_DAYS."""
    now = now or time.time()
    out, retry = set(), {}
    for e in evs:
        tid, ev = e.get("task_id"), e.get("event")
        if ev in ("ready", "accept", "reject"):
            out.add(tid)
        elif ev in ("abstain", "fail"):
            retry[tid] = max(retry.get(tid, 0), e.get("ts", 0))
    out |= {t for t, ts in retry.items() if now - ts < RETRY_AFTER_DAYS * 86400}
    return out


def filter_picks(arr: list, ids: set[str], allowed: dict, maxn: int) -> list[dict]:
    """The picker's JSON, minus unknown ids, disabled classes and low confidence."""
    out = []
    for p in arr if isinstance(arr, list) else []:
        try:
            conf = float(p.get("confidence") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if p.get("task_id") in ids and p.get("class") in allowed and conf >= MIN_CONFIDENCE:
            out.append(p)
    return out[:maxn]


# --- asks (second pass) --------------------------------------------------------
def parse_picker(text: str) -> tuple[list, list]:
    """(picks, near) from the picker's reply. New shape is an object
    {"picks": [...], "near": [...]}; a bare array (the 11-08 shape) is picks only.
    Raises ValueError when neither parses."""
    text = text or ""
    i_obj, i_arr = text.find("{"), text.find("[")
    if i_obj >= 0 and (i_arr < 0 or i_obj < i_arr):
        obj = json.loads(text[i_obj: text.rindex("}") + 1])
        if isinstance(obj, dict) and ("picks" in obj or "near" in obj):
            picks_, near = obj.get("picks") or [], obj.get("near") or []
            return (picks_ if isinstance(picks_, list) else []), (near if isinstance(near, list) else [])
        raise ValueError("picker object without picks/near")
    if i_arr < 0:
        raise ValueError("no JSON in picker reply")
    arr = json.loads(text[i_arr: text.rindex("]") + 1])
    return (arr if isinstance(arr, list) else []), []


def filter_near(arr: list, ids: set[str], maxn: int = MAX_ASKS) -> list[dict]:
    """Near-misses worth acting on: a known task, needs ∈ {fact, capability},
    a non-empty one-line question. Best first, at most maxn."""
    out = []
    for p in arr if isinstance(arr, list) else []:
        if not isinstance(p, dict) or p.get("task_id") not in ids:
            continue
        needs = str(p.get("needs") or "").strip().lower()
        q = " ".join(str(p.get("question") or "").split())
        if needs not in ("fact", "capability") or not q:
            continue
        out.append({"task_id": p["task_id"], "class": p.get("class"), "needs": needs,
                    "question": q[:300], "confidence": p.get("confidence")})
    return out[:maxn]


def desc_hash(text: str | None) -> str:
    """Stable hash of a description: edit-form saves desc.rstrip(), so an
    untouched ctrl-o save must not read as an answer."""
    import hashlib
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:12]


def open_asks(evs: list[dict], now: float | None = None) -> dict[str, dict]:
    """task_id -> its newest `ask` inside ASK_RETRY_DAYS with no later `answered`."""
    now = now or time.time()
    cutoff = now - ASK_RETRY_DAYS * 86400
    out: dict[str, dict] = {}
    for e in evs:
        tid, ev, ts = e.get("task_id"), e.get("event"), e.get("ts", 0)
        if not tid or ts < cutoff:
            continue
        if ev == "ask":
            out[tid] = e
        elif ev == "answered":
            out.pop(tid, None)
    return out


def asked_recently(evs: list[dict], now: float | None = None) -> set[str]:
    """Tasks asked, answered or flagged as a capability gap inside ASK_RETRY_DAYS:
    never a second 😈? on the same card in the window (the 22:00 picker tends
    to return a just-answered task as `near` again)."""
    now = now or time.time()
    cutoff = now - ASK_RETRY_DAYS * 86400
    return {e["task_id"] for e in evs
            if e.get("ts", 0) >= cutoff and e.get("task_id")
            and e.get("event") in ("ask", "answered", "capability_gap")}


def _comment_ts(c: dict) -> int:
    import calendar
    s = str(c.get("posted_at") or c.get("postedAt") or "")[:19]
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0


def answer_for(ask: dict, task: dict, comments: list[dict]) -> str | None:
    """JM's answer to an open ask, or None. The description changing since the
    ask counts (dtd ctrl-o notes field); so does any comment posted after the
    ask that is not an agent's (😈 …, dtd-short: …). The newest wins."""
    desc = (task.get("description") or "").strip()
    if desc_hash(desc) != ask.get("desc_hash"):
        return desc or None
    later = [c for c in comments or []
             if _comment_ts(c) > int(ask.get("ts", 0))
             and not str(c.get("content") or "").lstrip().startswith(AGENT_COMMENT_PREFIXES)
             and str(c.get("content") or "").strip()]
    if later:
        return str(max(later, key=_comment_ts).get("content")).strip()
    return None


def ask_stats(evs: list[dict], since: int = 0, until: int | None = None) -> dict[str, int]:
    """asks, answered, and how many answered tasks were then picked / accepted.
    The question pass pays only if answered → accepted; that is the number /1s watches."""
    until = until or 2 ** 62
    asked = {e["task_id"] for e in evs if e.get("event") == "ask" and since <= e.get("ts", 0) < until}
    answered = {e["task_id"] for e in evs if e.get("event") == "answered"
                and since <= e.get("ts", 0) < until and e["task_id"] in asked}
    picked = {e["task_id"] for e in evs if e.get("event") == "pick" and e["task_id"] in answered}
    accepted = {e["task_id"] for e in evs if e.get("event") == "accept" and e["task_id"] in answered}
    return {"asks": len(asked), "answered": len(answered), "answered_picked": len(picked),
            "answered_accepted": len(accepted)}


def outcome(text: str, is_error: bool, task_id: str, cls: str,
            root: Path | None = None) -> tuple[str, str]:
    """ready | abstain | fail, from the worker's final line and its receipt."""
    lines = (text or "").strip().splitlines()
    last = lines[-1].strip() if lines else ""
    if last.startswith("ABSTAIN"):
        return "abstain", last[len("ABSTAIN"):].lstrip(": ")
    if is_error:
        return "fail", (text or "")[-200:]
    ok, reason = validate_receipt(task_id, cls, root)
    return ("ready" if ok else "fail"), reason


# --- calibration -------------------------------------------------------------
def review_seconds(evs: list[dict], task_id: str, decided_ts: int) -> int | None:
    """Accept/reject time minus the first ctrl-o view (else the ready time)."""
    start = None
    for e in evs:
        if e.get("task_id") != task_id:
            continue
        if e.get("event") == "viewed" and (start is None or e["ts"] < start):
            start = e["ts"]
    if start is None:
        ready = [e["ts"] for e in evs if e.get("task_id") == task_id and e.get("event") == "ready"]
        start = max(ready) if ready else None
    return max(0, decided_ts - start) if start is not None else None


def class_stats(evs: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in evs:
        if e.get("event") not in ("accept", "reject"):
            continue
        s = out.setdefault(e.get("class") or "?", {"accept": 0, "reject": 0, "review_ratio": []})
        s[e["event"]] += 1
        rs, n = e.get("review_sec"), e.get("minutes")
        if rs is not None and n:
            s["review_ratio"].append(rs / (n * 60))
    return out


def disabled_classes(evs: list[dict]) -> dict[str, str]:
    out = {}
    for cls, s in class_stats(evs).items():
        decided = s["accept"] + s["reject"]
        if decided < MIN_DECIDED:
            continue
        rate = s["accept"] / decided
        if rate < MIN_ACCEPT_RATE:
            out[cls] = f"accept rate {rate:.0%} < {MIN_ACCEPT_RATE:.0%}"
        elif s["review_ratio"] and statistics.median(s["review_ratio"]) > REVIEW_SHARE:
            out[cls] = f"median review {statistics.median(s['review_ratio']):.0%} of (N) > {REVIEW_SHARE:.0%}"
    return out


# --- hooks called from dtd / did-fast ------------------------------------------
def on_viewed(task_id: str) -> None:
    """edit-form.py (dtd ctrl-o) opened a card with a ready receipt."""
    if status(str(task_id)) == "ready":
        log("viewed", task_id)


def _pick_meta(evs: list[dict], task_id: str) -> dict:
    for e in reversed(evs):
        if e.get("task_id") == task_id and e.get("event") in ("ready", "pick"):
            return e
    return {}


def on_close(task_id: str) -> bool:
    """did-fast closed a task. If it had a ready receipt, that close is JM's accept:
    log it, and hand any code branch to merge.py (detached; did-fast is latency-bound)."""
    task_id = str(task_id)
    if status(task_id) != "ready":
        return False
    evs = events()
    now = int(time.time())
    meta = _pick_meta(evs, task_id)
    log("accept", task_id, **{"class": meta.get("class"), "minutes": meta.get("minutes"),
                              "value": meta.get("value"),
                              "review_sec": review_seconds(evs, task_id, now)})
    set_status(task_id, "accepted")
    if branch(task_id):
        subprocess.Popen(["/usr/bin/python3", str(Path(__file__).with_name("merge.py")), task_id],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    return True
