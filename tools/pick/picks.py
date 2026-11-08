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

MAX_PICKS = 3
MIN_CONFIDENCE = 0.7
MIN_DECIDED = 3
MIN_ACCEPT_RATE = 0.7
REVIEW_SHARE = 1 / 3          # review may cost at most this share of the task's (N)
RETRY_AFTER_DAYS = 7          # an abstained/failed task is not re-picked for this long

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
    if cls == "code" and not ((d / "diff.patch").exists() and branch(task_id, root)):
        return False, "code receipt needs diff.patch and a branch file"
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
