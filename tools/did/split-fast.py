#!/usr/bin/env python3
"""split-fast — "done so far" (terminal dtd's ctrl-p) as a standalone,
non-interactive script with JSON on stdout (2026-10-22, for dtd.py's
POST /api/split; the iOS app calls that).

A port of the python body of dtd.sh's generated /tmp/dtd-$ID.split.sh (the
SPLITEOF heredoc), with the same inputs and the same Todoist + did-fast work:

  1. a completed posthoc record "<done name> (min) [pts] {bonus}" (labels +
     posthoc, same project, due today), closed at once
  2. the original task renamed to its remainder "<name> (min) [pts] {bonus}"
     and kept on the do-date (default today), or closed when --no-keep
  3. did-fast --points-only credits [pts] with the task's 0分 domain label
     (个's label when it has none) and {bonus} alone (0g column)
  4. completed-today ties the done name to the posthoc id, so a remainder
     sharing the name is not hidden by name on other dtds

dtd.sh is untouched; keep the two in step when either changes. Differences,
all deliberate: the Todoist token comes from ~/.config/todoist/token (the
heredoc hardcodes it), "today" is lib/daytime's (travel-aware, as dtd.py's),
and the ctrl-z undo journal is only written when --journal is given (the
journal is per terminal-dtd session; the phone has no undo).

Usage:
  split-fast.py --id ID [--cache task-queue.json] [--pts N] [--bonus N]
                [--minutes N] [--name DONE_NAME] [--rem-name S] [--rem-pts N]
                [--rem-min N] [--rem-bonus N] [--rem-due YYYY-MM-DD]
                [--no-keep] [--journal PATH] [--dry-run]
stdout: {"ok": bool, "message": str, ...}; exit 0 when ok, 1 otherwise.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))
DID_FAST = HERE / "did-fast.py"
EDIT_FAST = HERE / "edit-fast.py"
UNDO_FAST = HERE / "undo-fast.py"
MARK_COMPLETED = HERE / "mark-completed.py"
CACHE = Path.home() / ".local/state/jm/task-queue.json"
TOKEN_FILE = Path.home() / ".config/todoist/token"
BASE = "https://api.todoist.com/api/v1"
FALLBACK_DOMAINS = {'i9', 'i447', 'f693', 'f694', 'm5x2', 'm828', 'g245', 'infra', 'cc', 'hcmc',
                    'hcm', 'hci', 'hcb', 'hcbp', 'xk87', 'xk88', 'xk20', 'xk22', 's897'}


def _today() -> str:
    try:
        import daytime
        return daytime.today_iso()
    except Exception:  # noqa: BLE001
        return _dt.date.today().isoformat()


def clean_name(content: str) -> str:
    """split.sh's sed: drop (N), [N], [x/m], {N}; squeeze; trim; cut at …."""
    c = re.sub(r" *\([0-9]*\)", "", content)
    c = re.sub(r" *\[[0-9]*\]", "", c)
    c = re.sub(r" *\[[0-9.+]*/m\]", "", c)
    c = re.sub(r" *\{[0-9]*\}", "", c)
    c = re.sub(r"  +", " ", c).rstrip()
    if "…" in c:
        c = c.split("…", 1)[0].rstrip()
    return c


def _first(pat: str, s: str) -> str:
    m = re.search(pat, s)
    return m.group(1) if m else ""


def plan(content: str, *, pts=None, bonus=None, minutes=None, done_name="", rem_name="",
         rem_pts=None, rem_min=None, rem_bonus=None, rem_due="", keep=True,
         today: str | None = None) -> dict:
    """Pure: the task's current content + the form's answers -> what split.sh
    would write. Same arithmetic, tag order and defaults as the heredoc."""
    today = today or _today()
    clean = clean_name(content)
    total = int(_first(r"\[([0-9]+)\]", content) or 0)
    bonus_total = int(_first(r"\{([0-9]+)\}", content) or 0)
    duration = _first(r"\(([0-9]+)\)", content)
    pts_today = int(pts) if pts is not None else 0
    bonus_today = int(bonus) if bonus is not None else 0
    remaining_pts = max(0, total - pts_today) if total > 0 else 0
    if rem_pts is not None:
        remaining_pts = int(rem_pts)
    remaining_bonus = max(0, bonus_total - bonus_today) if bonus_total > 0 else 0
    if rem_bonus is not None:
        remaining_bonus = int(rem_bonus)
    done_dur = remaining_dur = duration
    if minutes is not None:
        done_dur = str(int(minutes))
        remaining_dur = str(max(0, int(duration) - int(minutes))) if duration.isdigit() else ""
    if rem_min is not None:
        remaining_dur = str(int(rem_min))
    done_tags = [f"({done_dur or pts_today or bonus_today})"]
    if pts_today > 0:
        done_tags.append(f"[{pts_today}]")
    if bonus_today > 0:
        done_tags.append(f"{{{bonus_today}}}")
    rem_date = rem_due if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", rem_due or "") else today
    tags = []
    if remaining_dur and remaining_dur != "0":
        tags.append(f"({remaining_dur})")
    if remaining_pts > 0:
        tags.append(f"[{remaining_pts}]")
    if remaining_bonus > 0:
        tags.append(f"{{{remaining_bonus}}}")
    return {
        "clean": clean,
        "pts_today": pts_today, "bonus_today": bonus_today,
        "posthoc_content": " ".join([done_name or clean] + done_tags),
        "new_content": " ".join([rem_name or clean] + tags),
        "rem_tags": tags,
        "rem_date": rem_date,
        "keep": bool(keep),
        "today": today,
        # did-fast splits on , and ; -- a name holding one scattered the
        # points (split.sh regression 2026-06-06). Fullwidth ，； too: did-fast
        # has split on them since 2026-10-22 (45c6b881).
        "safe_name": re.sub(r"[,;，；]+", " ", clean),
    }


def message(p: dict, didfast_out, didfast_bonus) -> str:
    """split.sh's header line, verbatim shape."""
    pts_today, bonus_today, tags = p["pts_today"], p["bonus_today"], p["rem_tags"]
    got = " ".join(([f"[{pts_today}]"] if pts_today > 0 else [])
                   + ([f"{{{bonus_today}}}"] if bonus_today > 0 else [])) or "0"
    left = " ".join(tags[1:] if tags and tags[0].startswith("(") else tags) or "nothing"
    where = "kept for today" if p["rem_date"] <= p["today"] else f"deferred to {p['rem_date']}"
    msg = (f"✂ +{got} today / {left} {where}" if p["keep"]
           else f"✂ +{got} today · rest dropped, task closed")
    lost = []
    if pts_today > 0 and not (didfast_out or {}).get("results"):
        lost.append(f"[{pts_today}] (did-fast: no domain)")
    if bonus_today > 0 and not (didfast_bonus or {}).get("results"):
        lost.append(f"{{{bonus_today}}}")
    if lost:
        msg = "⚠ split: " + " ".join(lost) + " NOT logged / " + msg[2:]
    return msg


def _token() -> str:
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        return os.environ.get("TODOIST_API_KEY", "")


def api(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                                 headers={"Authorization": f"Bearer {_token()}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def credit(item: str):
    """did-fast --points-only (no Todoist matching: without it did-fast
    re-finds the just-renamed remainder and closes it)."""
    df = subprocess.run([sys.executable, str(DID_FAST), "--points-only", item],
                        capture_output=True, text=True, timeout=30)
    try:
        return json.loads(df.stdout)
    except Exception:  # noqa: BLE001
        return None


def domain_label(labels: list[str]) -> str:
    """The [N] credit's @label: the first label with a 0分 column (did-fast's
    own LABEL_TO_0FEN, never a hardcoded copy: bug 2026-10-09), else 个's
    (bug 2026-10-22: a qz12 split lost its [20]), else @g245."""
    df = None
    try:
        spec = importlib.util.spec_from_file_location("did_fast_split", DID_FAST)
        df = importlib.util.module_from_spec(spec)
        sys.modules["did_fast_split"] = df
        spec.loader.exec_module(df)
        domains = set(df.LABEL_TO_0FEN) - {"0g"}
    except Exception:  # noqa: BLE001
        domains = FALLBACK_DOMAINS
    for l in labels:
        if l in domains:
            return f"@{l}"
    try:
        ge = df.neon_cols.domain_col("0分", "个")
        lab = next((f"@{k}" for k, v in df.LABEL_TO_0FEN.items() if v == ge and k in domains), "")
    except Exception:  # noqa: BLE001
        lab = ""
    return lab or "@g245"


def tie_completed(name: str, posthoc_id: str) -> None:
    """completed-today: the done name hides by the posthoc's id only."""
    spec = importlib.util.spec_from_file_location("mc_split", MARK_COMPLETED)
    mc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mc)
    mc.append_names([name], ids={name: posthoc_id})


def _cache_content(cache_file: Path, task_id: str) -> str:
    try:
        d = json.loads(Path(cache_file).read_text())
    except (OSError, ValueError):
        return ""
    for v in d.values():
        if isinstance(v, list):
            for t in v:
                if isinstance(t, dict) and str(t.get("id")) == str(task_id):
                    return t.get("content") or ""
    return ""


def run(task_id: str, *, cache_file: Path = CACHE, pts=None, bonus=None, minutes=None,
        done_name="", rem_name="", rem_pts=None, rem_min=None, rem_bonus=None, rem_due="",
        keep=True, journal: str = "", dry_run=False, api=api, credit=credit,
        label_for=domain_label, tie=tie_completed) -> dict:
    if not (isinstance(pts, int) and pts >= 0) and not (isinstance(bonus, int) and bonus >= 0):
        return {"ok": False, "error": "split needs pts (or bonus) done"}
    try:
        task = api("GET", f"/tasks/{task_id}")
    except Exception:  # noqa: BLE001
        task = None
    if not task or not task.get("id"):
        return {"ok": False, "error": "split: task not found"}
    # split.sh reads name/[N]/(N) from the dtd cache (what the user sees);
    # the live Todoist content is the fallback for a task the cache lacks.
    content = _cache_content(cache_file, task_id) or task.get("content", "")
    p = plan(content, pts=pts, bonus=bonus, minutes=minutes, done_name=done_name,
             rem_name=rem_name, rem_pts=rem_pts, rem_min=rem_min, rem_bonus=rem_bonus,
             rem_due=rem_due, keep=keep)
    if dry_run:
        return {"ok": True, "dry_run": True, "message": "dry run", **p}
    tid = task["id"]
    labels = task.get("labels", [])
    # 1. the done record, closed at once
    posthoc = api("POST", "/tasks", {"content": p["posthoc_content"],
                                     "labels": labels + ["posthoc"],
                                     "due_date": p["today"],
                                     "project_id": task.get("project_id")})
    if posthoc:
        api("POST", f"/tasks/{posthoc['id']}/close")
    # 2. remainder renamed + kept on the do-date, or the task closed
    if p["keep"]:
        api("POST", f"/tasks/{tid}", {"content": p["new_content"], "due_date": p["rem_date"]})
    else:
        api("POST", f"/tasks/{tid}/close")
    # 3. credit: [N] with its domain label; {N} alone (a label double-credits)
    didfast_out = didfast_bonus = None
    if p["pts_today"] > 0:
        didfast_out = credit(f"{p['safe_name']} [{p['pts_today']}] {label_for(labels)}")
    if p["bonus_today"] > 0:
        didfast_bonus = credit(f"{p['safe_name']} {{{p['bonus_today']}}}")
    # 4. tie the done name to the posthoc id (split.sh bug 2026-10-22)
    if posthoc and p["keep"] and (didfast_out or didfast_bonus):
        try:
            tie(p["safe_name"].strip(), posthoc["id"])
        except Exception as e:  # noqa: BLE001
            print(f"split: completed-today id tie failed: {e}", file=sys.stderr)
    if journal:
        record = {"type": "split", "names": [p["clean"]], "task_id": tid,
                  "prev_content": task.get("content", ""),
                  "prev_due": (task.get("due") or {}).get("date", ""),
                  "posthoc_id": posthoc["id"] if posthoc else None,
                  "didfast": didfast_out, "didfast_bonus": didfast_bonus,
                  "closed": not p["keep"]}
        subprocess.run([sys.executable, str(UNDO_FAST), "--append", journal],
                       input=json.dumps(record, ensure_ascii=False), text=True,
                       capture_output=True, timeout=10)
    msg = message(p, didfast_out, didfast_bonus)
    return {"ok": True, "message": msg, "task_id": tid,
            "posthoc_id": posthoc["id"] if posthoc else None,
            "new_content": p["new_content"], "rem_date": p["rem_date"],
            "closed": not p["keep"], "lost": msg.startswith("⚠")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="dtd split (done so far), non-interactive")
    ap.add_argument("--id", required=True)
    ap.add_argument("--cache", default=str(CACHE))
    for flag in ("--pts", "--bonus", "--minutes", "--rem-pts", "--rem-min", "--rem-bonus"):
        ap.add_argument(flag, type=int)
    ap.add_argument("--name", default="", help="done record name (blank = the task's)")
    ap.add_argument("--rem-name", default="", help="remainder name (blank = keep)")
    ap.add_argument("--rem-due", default="", help="remainder do-date (blank = today)")
    ap.add_argument("--no-keep", action="store_true", help="close the task: partial credit")
    ap.add_argument("--journal", default="", help="ctrl-z undo journal to append to")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        res = run(a.id, cache_file=Path(a.cache), pts=a.pts, bonus=a.bonus, minutes=a.minutes,
                  done_name=a.name.strip(), rem_name=a.rem_name.strip(), rem_pts=a.rem_pts,
                  rem_min=a.rem_min, rem_bonus=a.rem_bonus, rem_due=a.rem_due.strip(),
                  keep=not a.no_keep, journal=a.journal, dry_run=a.dry_run)
    except Exception as e:  # noqa: BLE001 -- Todoist HTTP error mid-split etc.
        res = {"ok": False, "error": f"split failed: {e}"}
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
