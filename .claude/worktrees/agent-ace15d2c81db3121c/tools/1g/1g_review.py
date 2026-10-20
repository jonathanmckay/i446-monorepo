#!/usr/bin/env python3
"""1g_review.py: score last week's 1g goals from the 2g+1g narrative doc.

    1g_review.py [YYYY-MM-DD]            evaluate → JSON on stdout
    1g_review.py --apply verdicts.json   write sub-bullet verdicts into the doc

Week = Sunday–Saturday. No date = the most recently completed week; a date
picks the week containing it. Goals are read from the week's block in
~/vault/g245/5e2/YYYY.MM 2g+1g-narrative.md (the month the Sunday falls in):

    - **Oct 4 – Oct 10** —
        - m5x2
            - 600分

Evaluators (anything else comes back status "❓" with evidence for the
/1g skill to judge or ask JM about):
  * "<N>分" goals  → sum of that domain's daily points (dashboard points
    cache, the same per-day Neon 0分 totals /0t refreshes), vs N.
  * "<N> days good media" → days whose 0n `1+` (AU) cell is blank or ≤ 0,
    i.e. no junk (positive-tag) media logged that day.
Each goal also carries the week's completed Todoist tasks for its domain.
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MONO = Path.home() / "i446-monorepo"
sys.path.insert(0, str(MONO / "lib"))

TZ = ZoneInfo("America/Los_Angeles")
NARRATIVE_DIR = Path.home() / "vault" / "g245" / "5e2"
POINTS_CACHE = MONO / "tools" / "personal-dashboard" / ".points-cache.json"
MARK = "<!--1g-->"

# narrative domain → points-cache label (Neon 0分 column header)
DOMAIN_LABEL = {
    "i9": "i9", "i447": "i9", "f693": "i9", "f694": "i9",
    "m5x2": "m5", "m5c7": "m5",
    "hcmc": "媒", "hcm": "思", "hcmp": "思",
    "hcb": "hcb", "hcbp": "hcb",
    "xk87": "xk", "xk88": "xk", "s897": "社",
    "g245": "0₲", "0g": "0₲", "m828": "个",
}
OK_RATIO, PARTIAL_RATIO = 1.0, 0.8
STOPWORDS = {"thing", "need", "needed", "with", "that", "this", "from", "have",
             "done", "make", "more", "less", "good", "week", "day"}


# ── week + doc ───────────────────────────────────────────────────────────────

def week_bounds(arg: str | None, today: date | None = None) -> tuple[date, date]:
    today = today or datetime.now(TZ).date()
    if arg:
        d = date.fromisoformat(arg)
        sun = d - timedelta(days=(d.weekday() + 1) % 7)
    else:
        this_sun = today - timedelta(days=(today.weekday() + 1) % 7)
        sun = this_sun - timedelta(days=7)
    return sun, sun + timedelta(days=6)


def doc_path(sun: date) -> Path:
    return NARRATIVE_DIR / f"{sun:%Y.%m} 2g+1g-narrative.md"


def _heading_re(sun: date, sat: date) -> re.Pattern:
    a = f"{sun:%b} {sun.day}"
    b = f"{sat:%b} {sat.day}"
    return re.compile(rf"^\s*-\s*\**\s*{re.escape(a)}\s*[–—-]+\s*{re.escape(b)}\b")


def _indent(line: str) -> int:
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


def parse_week_goals(lines: list[str], sun: date, sat: date) -> list[dict]:
    """[{domain, text, line_no}] for the week's block. line_no is 0-based."""
    rx = _heading_re(sun, sat)
    start = next((i for i, l in enumerate(lines) if rx.search(l)), None)
    if start is None:
        raise SystemExit(f"week heading for {sun:%b} {sun.day} – {sat:%b} {sat.day} not found")
    base = _indent(lines[start])
    goals, domain, dom_indent = [], None, None
    for i in range(start + 1, len(lines)):
        l = lines[i]
        if not l.strip():
            continue
        ind = _indent(l)
        if ind <= base or l.lstrip().startswith("#"):
            break
        text = l.strip().lstrip("-*").strip()
        if MARK in l:
            continue
        if domain is None or ind <= dom_indent:
            domain, dom_indent = text.split()[0] if text else "", ind
            continue
        if ind > dom_indent and text:
            goals.append({"domain": domain, "text": text, "line_no": i})
    return goals


# ── data sources ─────────────────────────────────────────────────────────────

def _on_ix() -> bool:
    return socket.gethostname().lower().startswith("ix")


def load_points() -> dict:
    if _on_ix():
        return json.loads(POINTS_CACHE.read_text())
    out = subprocess.run(["ssh", "ix", f"cat {POINTS_CACHE}"],
                         capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise SystemExit(f"points cache unreadable on ix: {out.stderr.strip()[:200]}")
    return json.loads(out.stdout)


def week_days(sun: date) -> list[date]:
    return [sun + timedelta(days=i) for i in range(7)]


# points-cache label → Neon 0分 header, for the live fallback read.
LABEL_0FEN_HEADER = {"i9": "i9", "m5": "m5", "媒": "媒", "思": "思", "hcb": "hcb",
                     "xk": "xk", "社": "社", "0₲": "0g", "个": "个"}


def _neon_day_value(label: str, d: date) -> float | None:
    """Live 0分 cell for one day. The points cache keeps only POSITIVE
    day-values, so a net-negative day (hcb on a docked night) is simply
    absent there; this reads the real number."""
    try:
        from neon import excel, cols
        col = cols.col("0分", LABEL_0FEN_HEADER[label])
        r = excel.read("0分", col, date=f"{d.month}/{d.day}")
        if not r.get("ok"):
            r = excel.read("0分", col, date=f"{d.month}/{d.day}")
        v = r.get("value") if r.get("ok") else None
        return float(v) if v not in (None, "") else 0.0
    except Exception:
        return None


def domain_points(points: dict, label: str, days: list[date]) -> tuple[int, list[str]]:
    total, missing = 0.0, []
    for d in days:
        day = points.get(d.isoformat())
        if day is not None and label in day:
            total += float(day[label] or 0)
            continue
        live = _neon_day_value(label, d)
        if live is None:
            missing.append(d.isoformat())
        else:
            total += live
    return round(total), missing


def good_media_days(days: list[date]) -> tuple[int, dict]:
    from neon import excel  # lazy: only this evaluator needs Excel
    per_day = {}
    for d in days:
        r = excel.read("0n", "AU", date=f"{d.month}/{d.day}")
        if not r.get("ok"):
            r = excel.read("0n", "AU", date=f"{d.month}/{d.day}")  # one retry
        if not r.get("ok"):
            per_day[d.isoformat()] = "unreadable"
            continue
        v = r.get("value")
        try:
            num = float(v) if v not in (None, "") else 0.0
        except ValueError:
            num = 0.0
        per_day[d.isoformat()] = num
    good = sum(1 for v in per_day.values() if isinstance(v, float) and v <= 0)
    return good, per_day


def completed_tasks(sun: date, sat: date) -> list[dict]:
    """Todoist completions in the PT week, one request per day (the endpoint
    caps at 200 items per window; see 1-1n/make_heatmap.py)."""
    try:
        import todoist
        token = todoist._token()
    except Exception:
        return []
    items, seen = [], set()
    d = sun - timedelta(days=1)
    while d <= sat + timedelta(days=1):
        url = (f"https://api.todoist.com/api/v1/tasks/completed?since={d}T00:00:00Z"
               f"&until={d + timedelta(days=1)}T00:00:00Z&limit=200")
        while url:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
            except Exception:
                break
            projects = data.get("projects") or {}
            for it in data.get("items", []):
                key = (it.get("id") or it.get("task_id"), it.get("completed_at"))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    loc = datetime.fromisoformat(it["completed_at"].replace("Z", "+00:00")).astimezone(TZ)
                except Exception:
                    continue
                if sun <= loc.date() <= sat:
                    content = it.get("content", "")
                    proj = (projects.get(it.get("project_id")) or {}).get("name", "")
                    # The completed endpoint drops labels; domains come from the
                    # project (named after domain codes) and any @code in the text.
                    doms = {proj} | set(re.findall(r"@([\w#-]+)", content))
                    items.append({"content": content, "project": proj,
                                  "domains": sorted(d for d in doms if d),
                                  "date": loc.date().isoformat()})
            nxt = data.get("next_cursor")
            url = (url.split("&cursor=")[0] + f"&cursor={nxt}") if nxt else None
        d += timedelta(days=1)
    return items


# ── evaluation ───────────────────────────────────────────────────────────────

POINTS_RX = re.compile(r"(\d[\d,]*)\s*分")
MEDIA_RX = re.compile(r"(\d+)\s*days?\s+good\s+media", re.I)


def _status(actual: float, target: float) -> str:
    if target <= 0:
        return "❓"
    r = actual / target
    return "✅" if r >= OK_RATIO else ("🟡" if r >= PARTIAL_RATIO else "❌")


def _stem(w: str) -> str:
    w = w.lower()
    if w.endswith("es") and len(w) > 4:
        return w[:-2]
    if w.endswith("s") and len(w) > 3:
        return w[:-1]
    return w


def evaluate(goal: dict, ctx: dict) -> dict:
    text, dom = goal["text"], goal["domain"]
    clean = re.sub(r"\{\d+\}", "", text).strip()
    out = dict(goal)
    out["evidence"] = [{"date": t["date"], "content": t["content"]}
                       for t in ctx["tasks"] if dom in t["domains"]][:40]

    m = MEDIA_RX.search(clean)
    if m:
        target = int(m.group(1))
        good, per_day = good_media_days(ctx["days"])
        out.update(kind="good_media", target=target, actual=good, per_day=per_day,
                   status=_status(good, target),
                   note=f"{good}/{target} days with no junk (#1) media")
        return out

    m = POINTS_RX.search(clean)
    label = DOMAIN_LABEL.get(dom)
    if m and label:
        target = int(m.group(1).replace(",", ""))
        actual, missing = domain_points(ctx["points"], label, ctx["days"])
        rest = POINTS_RX.sub("", clean, count=1).strip(" -→>")
        out.update(kind="points", target=target, actual=actual, label=label,
                   status=_status(actual, target),
                   note=f"{actual:,}分 of {target:,}分 ({round(100 * actual / target)}%)")
        if missing:
            out["note"] += f"; no data for {', '.join(missing)}"
        if rest:
            # More than a bare points target: the number is scored, the rest
            # needs a human read (e.g. "-> weight should be 192.x").
            out["unscored_remainder"] = rest
            out["needs_question"] = True
        return out

    # Free-text goal: add keyword hits from ANY domain (tax work may not be
    # filed under qz12), e.g. "taxes" → tasks mentioning "tax".
    words = {_stem(w) for w in re.findall(r"[A-Za-z]{4,}", clean)} - STOPWORDS
    if words:
        seen = {e["content"] for e in out["evidence"]}
        for t in ctx["tasks"]:
            low = t["content"].lower()
            if t["content"] not in seen and any(w in low for w in words):
                out["evidence"].append({"date": t["date"], "content": t["content"]})
                seen.add(t["content"])
    out.update(kind="manual", status="❓", note="", needs_question=True)
    return out


def run_eval(arg: str | None) -> dict:
    sun, sat = week_bounds(arg)
    path = doc_path(sun)
    if not path.exists():
        raise SystemExit(f"narrative doc not found: {path}")
    lines = path.read_text().splitlines()
    goals = parse_week_goals(lines, sun, sat)
    ctx = {"days": week_days(sun), "points": load_points(), "tasks": completed_tasks(sun, sat)}
    results = [evaluate(g, ctx) for g in goals]
    return {"week": f"{sun} – {sat}", "doc": str(path), "goals": results,
            "tasks_completed_total": len(ctx["tasks"])}


# ── apply ────────────────────────────────────────────────────────────────────

def apply(verdicts_path: str) -> str:
    """verdicts.json: {"doc": path, "verdicts": [{"line_no", "text", "status", "note"}]}.
    Writes/replaces one `- <emoji> <note> <!--1g-->` child bullet under each
    goal. Goals are re-located by text (line numbers can drift if JM edits the
    doc in between); line_no is only a tiebreaker."""
    spec = json.loads(Path(verdicts_path).read_text())
    path = Path(spec["doc"])
    lines = path.read_text().splitlines()
    # Bottom-up so inserts don't shift pending targets.
    for v in sorted(spec["verdicts"], key=lambda v: -v.get("line_no", 0)):
        cands = [i for i, l in enumerate(lines) if l.strip().lstrip("-*").strip() == v["text"]]
        if not cands:
            raise SystemExit(f"goal line not found: {v['text']!r}")
        i = min(cands, key=lambda c: abs(c - v.get("line_no", c)))
        goal_ws = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
        child = f"{goal_ws}\t- {v['status']} {v['note'].strip()} {MARK}"
        j = i + 1
        while j < len(lines) and MARK in lines[j]:
            del lines[j]
        lines.insert(i + 1, child)
    text = "\n".join(lines) + "\n"
    text = re.sub(r"^updated: .*$", f"updated: {datetime.now(TZ).date()}", text, count=1, flags=re.M)
    path.write_text(text)
    return f"wrote {len(spec['verdicts'])} verdicts → {path}"


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--apply"]:
        print(apply(args[1]))
    else:
        print(json.dumps(run_eval(args[0] if args else None), ensure_ascii=False, indent=1))
