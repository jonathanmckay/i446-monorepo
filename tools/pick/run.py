#!/usr/bin/env python3
"""run.py — the agent-picks run (launchd com.jm.agent-pick, 06:00 and 22:00 on Ix).

    run.py [--dry-run] [--max N] [--only <task_id>]
    run.py --ask-only <task_id>        capture time, from /todo

Two passes a day (2026-11-09). The picker also returns near-misses: tasks it
would take if JM supplied one fact. Those get a `😈? <question>` comment and
are parked at the top of dtd (🕐). JM answers in the ctrl-o notes field or as
a plain comment; the next run reads the answer (detect_answers), shows it to
the picker as `jm_answer`, and works the task if it now qualifies. Capability
gaps (a repo or system the worker can't reach) go to the alert sink instead.

1. preflight: a headless `claude --print` must answer, else alert and stop
   (Ix's headless auth was silently logged out for months; see
   scripts/claude-ix-keepwarm.sh)
2. today|overdue tasks from Todoist → rule filter (picks.eligible) → drop
   classes calibration has disabled
3. one picker call (sonnet, no tools) chooses <= MAX_PICKS, each with a class
   and a proof plan
4. per pick, sequentially: claim (😈 in dtd), one worker call, then validate:
     valid receipt → label `agent`, receipt comment on the task, ✅ (mark_done)
     abstain / invalid / timeout → claim cleared, card looks normal again

Workers never run with --dangerously-skip-permissions. They get an explicit
allowlist (worker_permissions): write only in their worktree and receipt dir,
read anywhere, Gmail drafts but no send tool of any kind. Anything outside the
list is denied, since a headless run has nobody to approve a prompt.

--dry-run prints the candidates and the picker's choice without working them.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "lib"))
import picks  # noqa: E402
import todoist  # noqa: E402

_spec = importlib.util.spec_from_file_location("agent_claims", HERE.parent / "did" / "agent_claims.py")
agent_claims = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent_claims)

CLAUDE = "/opt/homebrew/bin/claude"
PICKER_BUDGET = "0.50"
WORKER_BUDGET = "3.00"
WORKER_TIMEOUT = 40 * 60
WORKTREE_CLASSES = ("code", "dashboard")

# Read-only MCP tools plus Gmail drafts. Everything that sends, posts, deletes
# or writes to a shared system is absent here AND listed in DENY.
READ_MCP = [
    "mcp__appfolio-mcp", "mcp__todoist__find-tasks", "mcp__todoist__fetch-object",
    "mcp__todoist__find-comments", "mcp__toggl_server__toggl_range", "mcp__toggl_server__toggl_date",
    "mcp__quickbooks-mcp__get_profit_loss", "mcp__quickbooks-mcp__get_report",
    "mcp__quickbooks-mcp__query", "mcp__quickbooks-mcp__get_balance_sheet",
    "mcp__workspace-mcp__get_doc_as_markdown", "mcp__workspace-mcp__read_sheet_values",
    "mcp__workspace-mcp__search_drive_files", "mcp__workspace-mcp__get_drive_file_content",
    "mcp__workspace-mcp__search_gmail_messages", "mcp__workspace-mcp__get_gmail_thread_content",
    "mcp__workspace-mcp__draft_gmail_message",
]
DENY = [
    "mcp__workspace-mcp__send_gmail_message", "mcp__workspace-mcp__send_message",
    "mcp__imessage_server__imessage_send", "mcp__todoist__complete-tasks",
    "mcp__todoist__add-tasks", "mcp__todoist__update-tasks", "mcp__todoist__delete-object",
    "mcp__quickbooks-mcp__create_bill", "mcp__quickbooks-mcp__create_journal_entry",
    "mcp__quickbooks-mcp__delete_entity", "mcp__workspace-mcp__set_drive_file_permissions",
    "mcp__workspace-mcp__manage_drive_access", "mcp__workspace-mcp__modify_sheet_values",
    "mcp__workspace-mcp__batch_update_doc", "mcp__workspace-mcp__modify_doc_text",
    "Bash(git push origin HEAD:wip*)", "Bash(git push origin HEAD:main*)",
    "Bash(git checkout*)", "Bash(git switch*)", "Bash(rm -rf*)", "Bash(curl*)",
]


def worker_permissions(rdir: Path, wt: Path | None, tid: str) -> list[str]:
    # File rules take `//abs/path` for an absolute path (a single `/` means
    # relative to the project root).
    allow = ["Read", "Grep", "Glob", "WebSearch", "WebFetch",
             f"Write(/{rdir}/**)", f"Edit(/{rdir}/**)"] + READ_MCP
    if wt:
        allow += [f"Write(/{wt}/**)", f"Edit(/{wt}/**)"]
        # the worker's cwd is the worktree, so plain and -C forms both appear
        for g in ("status", "diff", "log", "add", "commit"):
            allow += [f"Bash(git {g}*)", f"Bash(git -C {wt} {g}*)"]
        allow += [f"Bash(git push origin HEAD:agent/{tid})",
                  f"Bash(git -C {wt} push origin HEAD:agent/{tid})",
                  "Bash(python3 -m pytest*)", "Bash(ls*)"]
    return allow


def claude(prompt: str, *, model: str | None, budget: str, cwd: Path, timeout: int,
           allow: list[str] | None = None, add_dirs: list[Path] = ()) -> dict:
    cmd = [CLAUDE, "--print", "--output-format", "json", "--max-budget-usd", budget]
    if model:
        cmd += ["--model", model]
    if allow is not None:
        cmd += ["--allowedTools", ",".join(allow), "--disallowedTools", ",".join(DENY)]
    else:
        # The picker only reads its prompt. Also skip the user-scope MCP servers:
        # their tool schemas add ~185K tokens of system prompt, which blew the
        # picker's $0.50 budget on the first turn (2026-11-09 first run, $0.92,
        # empty result).
        cmd += ["--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
    for d in add_dirs:
        cmd += ["--add-dir", str(d)]
    # --ask-only runs from inside an interactive Claude session (/todo); a nested
    # `claude --print` refuses to start while CLAUDECODE & co. are inherited.
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           timeout=timeout, cwd=str(cwd), env=env)
    except subprocess.TimeoutExpired:
        return {"is_error": True, "result": "TIMEOUT", "total_cost_usd": None}
    try:
        out = json.loads(r.stdout)
    except ValueError:
        out = {"is_error": True, "result": (r.stdout or r.stderr or "").strip()[-500:]}
    if r.returncode != 0:
        out["is_error"] = True
    return out


def preflight() -> bool:
    out = claude("Reply with exactly: AUTHOK", model="haiku", budget="0.05",
                 cwd=Path.home(), timeout=120)
    ok = "AUTHOK" in str(out.get("result", "")) and not out.get("is_error")
    if not ok:
        picks.alert("auth", f"headless claude failed preflight: {str(out.get('result'))[:200]}")
        picks.log("preflight_fail", detail=str(out.get("result"))[:200])
    return ok


def candidates() -> list[dict]:
    q = urllib.parse.quote("(today | overdue)")
    out, cursor = [], None
    while True:
        path = f"/tasks/filter?query={q}&limit=200"
        if cursor:
            path += f"&cursor={urllib.parse.quote(cursor)}"
        page = todoist._request("GET", path)
        out += page.get("results", [])
        cursor = page.get("next_cursor")
        if not cursor:
            return out


PICKER_PROMPT = """You choose which of JM's Todoist tasks an autonomous agent should complete today, \
unattended, on his always-on Mac (Ix). JM reviews every result; a wrong pick costs him review time, \
so choose for precision, not coverage. Picking nothing is fine.

The agent can: change code in ~/i446-monorepo (in a git worktree, pushed to a review branch), read \
~/vault and the web, read AppFolio, QuickBooks, Google Docs/Sheets/Drive/Gmail, Todoist and Toggl, \
and save Gmail drafts. It can NOT: send or post anything, edit shared Docs/Sheets, make calls, attend \
meetings, reach Microsoft systems, or make judgment calls that are JM's.

A task qualifies only if ALL hold:
- the deliverable is concrete and the agent can finish it end to end with the access above
- its proof fits one class, and the agent can produce that proof:
{contract}
- JM could verify the proof in well under a third of the task's (N) minutes

A candidate with a "jm_answer" field is one you asked about earlier; JM has answered. Judge it with \
the answer in hand and prefer it when it now qualifies: he spent time answering.

Also report near-misses: tasks you would pick at confidence >= 0.7 if ONE thing were different. \
Most "review X", "fix X", "pull X" and "draft X" tasks are near-misses, missing one of three facts: \
the deliverable (reconcile the totals and list discrepancies? summarize? a draft reply?), the source \
(which doc, sheet, thread id, file), or the test for correct (which of two numbers is right, what \
counts as done). "needs": "fact" when JM could supply it in one line; "needs": "capability" when the \
agent lacks access or a tool (a repo outside ~/i446-monorepo, a system it can't reach), whatever JM \
says. Ask the one question whose answer would let you pick; it must be answerable in one line, and a \
task that needs a paragraph of context is not a near-miss. Up to {maxasks} near-misses, best first; \
report all you find up to that cap. Tasks that are JM's own judgment, calls, purchases or decisions \
are neither picks nor near-misses.

Candidates (JSON lines):
{cands}

Return ONLY a JSON object:
{{"picks": [{{"task_id": "...", "class": "code|dashboard|analysis|draft", "proof_plan": "one sentence: \
the exact proof JM will see", "confidence": 0.0-1.0}}],
 "near": [{{"task_id": "...", "class": "code|dashboard|analysis|draft", "confidence": 0.0-1.0, \
"needs": "fact|capability", "question": "one sentence JM can answer in one line"}}]}}
"picks" has at most {maxn} items, best first; both arrays may be empty.
"""

WORKER_PROMPT = """You are completing one of JM's Todoist tasks unattended. JM will review your result \
in his task list. He trusts nothing without proof: a claim you can't show, he must redo, which costs \
him more than if you had not tried.

TASK {task_id}: {content}
Description: {description}
Comments:
{comments}
JM's answer to the agent's earlier question (😈?): {answer}

Class: {cls}. Required proof: {contract}
Picker's proof plan: {plan}

STEP 1, feasibility gate (do this before any work): confirm you can reach every source you need and \
produce the proof above. Tools outside your allowlist are denied; don't fight that. If you can't \
finish, print a final line `ABSTAIN: <reason>` and stop. Abstaining costs JM nothing; a half-done \
result costs him a review.

STEP 2, do the work. Hard rules:
- Nothing is sent or posted; drafts only.
- Never close, create or re-price Todoist tasks; never edit [N] or (N).
- {where}

STEP 3, write the receipt to {rdir}/receipt.md with exactly these sections:
## Summary        (2-4 sentences: what is now done)
## Proof          (the evidence: quote sources with file:line / sheet!cell / URL; reference artifact files by name)
## How to verify  (one command, link or file JM can check in under a minute)
## Not done       (anything left, or "nothing")
Save every artifact (diff.patch, screenshots, query output, draft text) as files in {rdir}/.
{code_rule}
Final line of your output: `RECEIPT {rdir}/receipt.md` (or `ABSTAIN: <reason>`).
"""


def pick(cands: list[dict], maxn: int, disabled: dict[str, str]
         ) -> tuple[list[dict], list[dict], float | None]:
    """(picks, near-misses, cost). A candidate carrying `jm_answer` (set by
    detect_answers) is shown to the picker with that answer."""
    allowed = {k: v for k, v in picks.CONTRACT.items() if k not in disabled}
    if not allowed or not cands:
        return [], [], None
    lines = []
    for t in cands:
        rec = {"task_id": t["id"], "content": t["content"],
               "description": (t.get("description") or "")[:400], "labels": t.get("labels")}
        if t.get("jm_answer"):
            rec["jm_answer"] = t["jm_answer"]
        lines.append(json.dumps(rec, ensure_ascii=False))
    prompt = PICKER_PROMPT.format(contract="\n".join(f"  {k}: {v}" for k, v in allowed.items()),
                                  cands="\n".join(lines), maxn=maxn, maxasks=picks.MAX_ASKS)
    out = claude(prompt, model="sonnet", budget=PICKER_BUDGET, cwd=Path.home(), timeout=600)
    text, cost = str(out.get("result", "")), out.get("total_cost_usd")
    try:
        arr, near_raw = picks.parse_picker(text)
    except ValueError:
        picks.log("picker_fail", detail=text[:300])
        return [], [], cost
    ids = {t["id"] for t in cands}
    chosen = picks.filter_picks(arr, ids, allowed, maxn)
    near = picks.filter_near(near_raw, ids)
    if arr and not chosen:
        # The picker saw something but nothing cleared the bar: keep its
        # reasoning so an empty day is explainable from events.jsonl alone.
        picks.log("picker_abstain", detail=json.dumps(
            [{"task_id": p.get("task_id"), "class": p.get("class"),
              "confidence": p.get("confidence")} for p in arr if isinstance(p, dict)])[:600])
    return chosen, near, cost


# --- second pass: ask, then work once answered (2026-11-09) ---------------------
def ask(task: dict, item: dict) -> str:
    """Post `😈? <question>` on the task and park it: an agent claim set straight
    to released, which dtd draws at the top with 🕐 (waiting on JM) for a day
    with no dtd change. Same claim → released → drop-from-index sequence as
    closeshell.followup, so a later plain claim(…, ASK_SESSION) can't release
    every earlier ask. Never touches a task another session holds."""
    tid = str(task["id"])
    if tid in agent_claims.load():
        picks.log("ask_skipped", tid, reason="claimed")
        return "skipped"
    todoist.add_comment(tid, picks.ASK_PREFIX + item["question"])
    picks.log("ask", tid, **{"class": item.get("class"), "confidence": item.get("confidence"),
                             "question": item["question"],
                             "desc_hash": picks.desc_hash(task.get("description"))})
    agent_claims.claim(task, picks.ASK_SESSION, also=True)
    agent_claims.set_state(tid, "released")
    agent_claims._set_held(picks.ASK_SESSION,
                           [i for i in agent_claims.held(picks.ASK_SESSION) if i != tid],
                           agent_claims.CLAIMS_DIR)
    return "asked"


def capability_gap(task: dict, item: dict) -> None:
    """The agent lacks access or a tool, not a fact: that is a one-time infra fix,
    so it goes to the alert sink (/1-i446 reads it), not onto JM's card."""
    tid = str(task["id"])
    picks.alert("capability", f"{tid} {task.get('content', '')[:80]}: {item['question']}",
                severity="warning")
    picks.log("capability_gap", tid, **{"class": item.get("class"), "question": item["question"]})


def detect_answers(tasks: list[dict], evs: list[dict]) -> int:
    """For every candidate with an open ask, read JM's answer (description edit
    or plain comment) onto the task as `jm_answer`, log it, and un-park the card.
    Only the ask's own parked claim is cleared; a live /d claim stays."""
    opens = picks.open_asks(evs)
    if not opens:
        return 0
    claims = agent_claims.load()
    n = 0
    for t in tasks:
        tid = str(t["id"])
        ask_ev = opens.get(tid)
        if not ask_ev:
            continue
        try:
            comments = todoist.get_comments(tid)
        except Exception:  # noqa: BLE001
            comments = []
        ans = picks.answer_for(ask_ev, t, comments)
        if not ans:
            continue
        t["jm_answer"] = ans[:600]
        picks.log("answered", tid, answer=ans[:400])
        if claims.get(tid, {}).get("session") == picks.ASK_SESSION:
            agent_claims.clear_task(tid)
        n += 1
    return n


def handle_near(near: list[dict], byid: dict[str, dict], chosen: list[dict]) -> int:
    """Act on the picker's near-misses: facts become asks (MAX_ASKS per run),
    capabilities become alerts. Nothing picked this run or asked/answered inside
    ASK_RETRY_DAYS is asked again. Returns the number of questions posted."""
    skip = {c["task_id"] for c in chosen} | picks.asked_recently(picks.events())
    posted = 0
    for item in near:
        tid = item["task_id"]
        if tid in skip or tid not in byid:
            continue
        if item["needs"] == "capability":
            capability_gap(byid[tid], item)
            continue
        if posted >= picks.MAX_ASKS:
            break
        try:
            if ask(byid[tid], item) == "asked":
                posted += 1
        except Exception as e:  # noqa: BLE001
            picks.log("ask_fail", tid, reason=str(e)[:200])
    return posted


def ask_only(tid: str) -> int:
    """Capture time (/todo): is this one new task a near-miss? If so, ask now,
    while the context is in JM's head. Never works it; no preflight (its
    failure path raises a critical alert, and /todo also runs off Ix)."""
    task = todoist.get_task(tid)
    if not task or task.get("checked"):
        print(f"{tid}: no open task")
        return 1
    evs = picks.events()
    ok, why = picks.eligible(task, set(), picks.recently_attempted(evs))
    if not ok:
        print(f"{tid}: not eligible ({why})")
        return 0
    if tid in picks.asked_recently(evs) or tid in agent_claims.load():
        print(f"{tid}: already asked or claimed")
        return 0
    chosen, near, cost = pick([task], 1, picks.disabled_classes(evs))
    picks.log("ask_only", tid, picked=bool(chosen), near=[n["needs"] for n in near], picker_cost=cost)
    if chosen:
        print(f"{tid}: pickable as-is ({chosen[0]['class']} {chosen[0].get('confidence')}); "
              "the next run takes it")
        return 0
    posted = handle_near(near, {tid: task}, [])
    print(f"{tid}: " + (f"asked: {near[0]['question']}" if posted else
                        ("capability gap alerted" if near else "not a near-miss")))
    return 0


def _dirty() -> set[str]:
    out = set()
    for repo in (Path.home() / "vault", picks.REPO):
        r = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                           capture_output=True, text=True)
        out |= {f"{repo.name}/{l[3:]}" for l in r.stdout.splitlines()}
    return out


def _worktree(tid: str) -> Path:
    wt = Path(f"/private/tmp/agent-{tid}")
    subprocess.run(["git", "-C", str(picks.REPO), "fetch", "-q", "origin", "wip"], check=True)
    if wt.exists():
        subprocess.run(["git", "-C", str(picks.REPO), "worktree", "remove", "--force", str(wt)])
    subprocess.run(["git", "-C", str(picks.REPO), "worktree", "add", "-q", "--detach", str(wt),
                    "origin/wip"], check=True)
    return wt


def work(task: dict, p: dict) -> str:
    tid, cls = str(task["id"]), p["class"]
    minutes, value = picks.minutes_and_value(task["content"])
    rdir = picks.receipt_dir(tid)
    if rdir.exists():
        shutil.rmtree(rdir)
    rdir.mkdir(parents=True)
    picks.log("pick", tid, **{"class": cls, "minutes": minutes, "value": value,
                              "confidence": p.get("confidence"), "plan": p.get("proof_plan")})
    session = f"pick-{time.strftime('%Y%m%d')}-{tid}"
    agent_claims.claim(task, session)
    wt = _worktree(tid) if cls in WORKTREE_CLASSES else None
    if wt:
        where = (f"Change code ONLY in the git worktree {wt} (a detached checkout of wip); "
                 f"writes anywhere else are denied.")
        code_rule = (f"If you changed code: commit in {wt}, run `git -C {wt} push origin "
                     f"HEAD:agent/{tid}`, write `agent/{tid}` to {rdir}/branch, and save "
                     f"`git -C {wt} diff origin/wip` as {rdir}/diff.patch.")
    else:
        where = f"Read ~/vault and ~/i446-monorepo freely; you can write only inside {rdir}."
        code_rule = ""
    try:
        comments = "\n".join(f"- {c.get('content', '')[:400]}" for c in todoist.get_comments(tid)) or "(none)"
    except Exception:  # noqa: BLE001
        comments = "(unavailable)"
    prompt = WORKER_PROMPT.format(task_id=tid, content=task["content"],
                                  description=task.get("description") or "(none)",
                                  comments=comments, cls=cls, contract=picks.CONTRACT[cls],
                                  plan=p.get("proof_plan"), where=where, rdir=rdir,
                                  code_rule=code_rule, answer=task.get("jm_answer") or "(none)")
    before, t0 = _dirty(), time.time()
    out = claude(prompt, model=None, budget=WORKER_BUDGET, cwd=wt or rdir,
                 allow=worker_permissions(rdir, wt, tid),
                 add_dirs=[Path.home() / "vault", picks.REPO] + ([wt] if wt else []),
                 timeout=WORKER_TIMEOUT)
    secs, cost = int(time.time() - t0), out.get("total_cost_usd")
    text = str(out.get("result", ""))
    (rdir / "agent.log").write_text(text)
    side = sorted(s for s in _dirty() - before
                  if "z_ibx/agent-picks" not in s and "z_ibx/agent-claims" not in s)
    if wt:
        subprocess.run(["git", "-C", str(picks.REPO), "worktree", "remove", "--force", str(wt)])

    result, reason = picks.outcome(text, bool(out.get("is_error")), tid, cls)
    if result == "ready" and side:
        with (rdir / "receipt.md").open("a") as f:
            f.write("\n## Side effects observed\nFiles that changed in vault/monorepo during the run "
                    "(may include other processes):\n" + "".join(f"- {s}\n" for s in side))
    picks.log(result, tid, **{"class": cls, "minutes": minutes, "value": value, "sec": secs,
                              "cost": cost, "reason": reason, "side_effects": len(side)})
    if result == "ready":
        picks.set_status(tid, "ready")
        labels = sorted(set(task.get("labels") or []) | {"agent"})
        todoist._request("POST", f"/tasks/{tid}", {"labels": labels})
        todoist.add_comment(tid, picks.summary_comment(tid))
        agent_claims.mark_done(session, [tid])
        # mark_done queues a Stop-hook note for interactive sessions; no hook runs for this one
        (agent_claims.CLAIMS_DIR / "by-session" / f"{session}.note").unlink(missing_ok=True)
    else:
        picks.set_status(tid, "failed")
        agent_claims.clear_task(tid)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=picks.MAX_PICKS)
    ap.add_argument("--only")
    ap.add_argument("--ask-only", metavar="TASK_ID",
                    help="capture time (/todo): ask about this one task if it is a near-miss; never work it")
    a = ap.parse_args()
    if a.ask_only:
        return ask_only(a.ask_only)
    if not preflight():
        print("preflight failed: headless claude is not logged in")
        return 1
    evs = picks.events()
    disabled = picks.disabled_classes(evs)
    # Parked asks are claims too (released, 🕐); they must stay eligible or the
    # second pass never sees the answer.
    claimed = {t for t, r in agent_claims.load().items() if r.get("session") != picks.ASK_SESSION}
    recent = picks.recently_attempted(evs)
    tasks = candidates()
    answered = detect_answers(tasks, evs)
    if a.only:
        ok = [t for t in tasks if t["id"] == a.only]
    else:
        ok = [t for t in tasks if picks.eligible(t, claimed, recent)[0]]
    print(f"{len(tasks)} today|overdue, {len(ok)} eligible, {answered} answered; "
          f"disabled classes: {disabled or 'none'}")
    chosen, near, cost = pick(ok, a.max, disabled)
    picks.log("run", n_tasks=len(tasks), n_eligible=len(ok), n_answered=answered,
              picked=[c["task_id"] for c in chosen], near=[n["task_id"] for n in near],
              picker_cost=cost, disabled=disabled, dry_run=a.dry_run)
    byid = {t["id"]: t for t in ok}
    for c in chosen:
        print(f"  pick {c['task_id']} [{c['class']} {c.get('confidence')}] "
              f"{byid[c['task_id']]['content']} — {c.get('proof_plan')}")
    for n in near:
        print(f"  near {n['task_id']} [{n['needs']} {n.get('confidence')}] "
              f"{byid[n['task_id']]['content']} — {n['question']}")
    if a.dry_run:
        return 0
    asked = handle_near(near, byid, chosen)
    if asked:
        print(f"  asked {asked}")
    for c in chosen:
        try:
            res = work(byid[c["task_id"]], c)
        except Exception as e:  # noqa: BLE001  one bad pick must not strand a 😈 or stop the rest
            res = "fail"
            picks.log("fail", c["task_id"], **{"class": c["class"], "reason": f"runner: {e}"[:300]})
            picks.set_status(c["task_id"], "failed")
            agent_claims.clear_task(c["task_id"])
            subprocess.run(["git", "-C", str(picks.REPO), "worktree", "remove", "--force",
                            f"/private/tmp/agent-{c['task_id']}"], capture_output=True)
        print(f"  {c['task_id']}: {res}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
