#!/usr/bin/env python3
"""
End-to-end /did runner for registry-resolved habits (0n and 1n+ paths).

For one-off Todoist tasks (Step 5) and variable tasks (Step 6) — i.e. anything
route.py classifies as `step=unknown` — this script exits with code 2 and the
SKILL.md agent path takes over.

Usage:
    python3 run.py "stats i9"              # auto-detect Toggl minutes
    python3 run.py "stats i9 12"           # explicit minutes
    python3 run.py "早餐 0827-0843"         # time range → minutes + Toggl entry
    python3 run.py "1 s897"                # 1n+ path
    python3 run.py "0l 4/27"               # past-date posthoc

Steps performed for `step=0n`:
  1. Compute minutes (explicit / time-range / Toggl auto-detect / default 1)
  2. excel.append on 0n at neon_col, today's row, value `+<min>` (or formula for 0l/cumulative)
  3. Close matching Todoist task (label `0neon`, content word-overlap)
  4. Step C d359 bump (if any d359/<slug> labels)
  5. (if hasTimeRange) Toggl create entry
  6. Append name to completed-today.json
  7. Print one-line confirmation

For `step=1n+`:
  1. Compute M.W from target_date (Sunday-anchored)
  2. Read points from 1n+ row 3 of neon_col (or use cumulative_increment)
  3. Write points to 1n+!{neon_col}{week_row}
  4. Append `+'1n+'!{neon_col}{week_row}` to 0分 at fen_col, today's row
  5. Close 1neon-labeled Todoist task
  6. Append to completed-today.json
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))

from neon import excel  # noqa: E402
import registry  # noqa: E402
import todoist  # noqa: E402

VAULT = Path.home() / "vault"
COMPLETED_TODAY = VAULT / "z_ibx" / "completed-today.json"
TASK_QUEUE = VAULT / "z_ibx" / "task-queue.json"

TIME_RANGE_RE = re.compile(r"\b(\d{4})-(\d{4})\b")
PAST_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2})\s*$")


def _today_md() -> str:
    n = datetime.now()
    return f"{n.month}/{n.day}"


def _route(query: str, target_date: str) -> dict:
    """Call route.py and return its JSON output."""
    r = subprocess.run(
        ["python3", str(Path.home() / "i446-monorepo/tools/did/route.py"),
         query, "--target-date", target_date],
        capture_output=True, text=True, check=True,
    )
    return json.loads(r.stdout)


def _parse_input(raw: str) -> tuple[str, str, Optional[tuple], Optional[int]]:
    """Returns (query_for_routing, target_date, time_range_or_None, explicit_minutes_or_None)."""
    parts = raw.strip().split()
    target = _today_md()
    # past-date suffix?
    if parts and re.fullmatch(r"\d{1,2}/\d{1,2}", parts[-1]):
        target = parts[-1]
        parts = parts[:-1]
    if parts and parts[-1] == "yesterday":
        n = datetime.now() - timedelta(days=1)
        target = f"{n.month}/{n.day}"
        parts = parts[:-1]
    # time range?
    time_range = None
    for i, p in enumerate(parts):
        m = TIME_RANGE_RE.fullmatch(p)
        if m:
            time_range = (m.group(1), m.group(2))
            parts = parts[:i] + parts[i+1:]
            break
    # trailing pure-digit token = explicit minutes (e.g. "o314 66")
    explicit_minutes = None
    if parts and re.fullmatch(r"\d+", parts[-1]) and len(parts) > 1:
        explicit_minutes = int(parts[-1])
        parts = parts[:-1]
    return " ".join(parts), target, time_range, explicit_minutes


def _minutes_from_time_range(tr: tuple[str, str]) -> int:
    s, e = tr
    sh, sm = int(s[:2]), int(s[2:])
    eh, em = int(e[:2]), int(e[2:])
    return (eh * 60 + em) - (sh * 60 + sm)


def _auto_detect_minutes(toggl_desc: str, project_code: str) -> int:
    """Sum today's Toggl entries matching desc OR project. Default 1."""
    try:
        r = subprocess.run(
            ["python3", str(Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"), "today"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return 1
        # Parse "today" output — best-effort grep for matching descriptions
        total = 0
        for line in r.stdout.splitlines():
            # heuristic: lines mentioning the desc + a duration like "12m" or "1h 12m"
            if toggl_desc.lower() in line.lower() or f"@{project_code}" in line:
                m = re.search(r"(\d+)h\s*(\d+)m|(\d+)m\b", line)
                if m:
                    if m.group(1) and m.group(2):
                        total += int(m.group(1)) * 60 + int(m.group(2))
                    elif m.group(3):
                        total += int(m.group(3))
        return max(total, 1)
    except Exception:
        return 1


def _append_completed(name: str) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    data = {"date": today, "names": []}
    if COMPLETED_TODAY.exists():
        try:
            data = json.loads(COMPLETED_TODAY.read_text())
            if data.get("date") != today:
                data = {"date": today, "names": []}
        except Exception:
            pass
    if name.lower() not in [n.lower() for n in data["names"]]:
        data["names"].append(name.lower())
    COMPLETED_TODAY.parent.mkdir(parents=True, exist_ok=True)
    COMPLETED_TODAY.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _drop_from_queue(task_id: str) -> None:
    """Remove a closed task from task-queue.json so /next hides it immediately.

    Walks the four bucket sections (关键路径, 夜neon, 0neon, 1neon) since the cache
    groups tasks by label, not as a flat list.
    """
    if not TASK_QUEUE.exists():
        return
    try:
        q = json.loads(TASK_QUEUE.read_text())
        changed = False
        for k, v in list(q.items()):
            if isinstance(v, list):
                before = len(v)
                q[k] = [t for t in v if t.get("id") != task_id]
                if len(q[k]) != before:
                    changed = True
        if changed:
            q["updated"] = datetime.now().isoformat(timespec="seconds")
            TASK_QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _fire_refresh() -> None:
    """Fire-and-forget Todoist→cache refresh after a write."""
    try:
        subprocess.Popen(
            ["python3", str(Path.home() / "i446-monorepo/tools/did/refresh-cache.py")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def _word_overlap(query: str, content: str) -> float:
    qt = set(re.findall(r"\w+", query.lower()))
    ct = set(re.findall(r"\w+", content.lower()))
    if not qt:
        return 0.0
    return len(qt & ct) / len(qt)


def _find_and_close_todoist(label: str, query: str, aliases: list) -> str | None:
    """Find best-matching open task by word overlap. Close it. Return task content or None."""
    candidates = todoist.find_tasks(labels=[label], limit=100)
    best = None
    best_score = 0.0
    queries = [query] + aliases
    for t in candidates:
        for q in queries:
            score = _word_overlap(q, t.get("content", ""))
            if score > best_score:
                best, best_score = t, score
    if best and best_score >= 0.6:
        try:
            todoist.close_task(best["id"])
            _drop_from_queue(best["id"])
            return best.get("content")
        except Exception as e:
            print(f"  ✗ Todoist close failed: {e}", file=sys.stderr)
    return None


def _calc_mw(target_date: str) -> tuple[float, int]:
    """target_date 'M/D' → (M.W, week_row in 1n+)."""
    n = datetime.now()
    m, d = (int(x) for x in target_date.split("/"))
    year = n.year
    target = datetime(year, m, d)
    # If target is in the future relative to current month/day, use prev year
    if target > n + timedelta(days=180):
        target = datetime(year - 1, m, d)
    # weekday(): Mon=0..Sun=6
    sunday = target - timedelta(days=(target.weekday() + 1) % 7)
    M = sunday.month
    W = (sunday.day - 1) // 7 + 1
    mw = float(f"{M}.{W}")
    # Find the row in 1n+ where col B == "M.W" string
    week_str = f"{M}.{W}"
    out = excel.read("1n+", "B", row=1)  # not used; we need to find the row
    # Look up row by scanning
    for r in range(4, 60):
        cell = excel.read("1n+", "B", row=r)
        if cell.get("ok") and str(cell.get("value", "")).strip() in (week_str, f"{M}.{W:.1f}"[:-2]):
            return mw, r
    raise RuntimeError(f"M.W={week_str} not found in 1n+ col B")


def run_0n(d: dict, raw_input: str, target_date: str, time_range, explicit_minutes: Optional[int] = None) -> int:
    name = d["habit_name"]
    sheet = d["neon_sheet"]
    col = d["neon_col"]
    is_past = target_date != _today_md()

    # Past-date 0n → Step 6b posthoc (skip Neon write, just create+close posthoc Todoist)
    if is_past:
        content = f"{name} @posthoc @{datetime.now().year}-{target_date.replace('/', '-')}"
        try:
            t = todoist.create_task(content, labels=["posthoc", "0neon", d["domain"]],
                                    due_string=target_date)
            todoist.close_task(t["id"])
            print(f"  ✓ {name} (posthoc {target_date}) — Todoist created + closed")
            _append_completed(name)
            return 0
        except Exception as e:
            print(f"  ✗ posthoc Todoist failed: {e}", file=sys.stderr)
            return 1

    # Compute minutes — explicit > time range > Toggl auto-detect > 1
    if explicit_minutes is not None:
        minutes = explicit_minutes
    elif time_range:
        minutes = _minutes_from_time_range(time_range)
    else:
        toggl = d.get("toggl") or {}
        minutes = _auto_detect_minutes(toggl.get("desc", name), toggl.get("project", ""))

    # Write to 0n
    cumulative = d.get("cumulative", False)
    if cumulative:
        # Add to existing value: read, then write sum
        cur = excel.read(sheet, col, date=target_date)
        old = 0
        try:
            old = int(float(cur.get("value", 0) or 0))
        except (TypeError, ValueError):
            old = 0
        result = excel.write(sheet, col, date=target_date, value=str(old + minutes))
    else:
        result = excel.append(sheet, col, date=target_date, value=f"+{minutes}")
    if not result.get("ok"):
        print(f"  ✗ Excel write failed: {result}", file=sys.stderr)
        return 1
    verify = result.get("value")

    # Close Todoist
    closed = _find_and_close_todoist(d.get("todoist_label") or "0neon", name, d.get("aliases", []))

    # Toggl entry if time range
    if time_range:
        s, e = time_range
        try:
            subprocess.run(
                ["python3", str(Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"),
                 "create", d["toggl"]["desc"], s, e, d["toggl"]["project"]],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            pass

    _append_completed(name)
    _fire_refresh()
    msg = f"  ✓ {name} → {minutes} (today) verify={verify}"
    if closed:
        msg += f" + todoist closed"
    print(msg)
    return 0


def run_1n(d: dict, target_date: str) -> int:
    name = d["habit_name"]
    col = d["neon_col"]
    fen_col = d["fen_col"]

    try:
        mw, week_row = _calc_mw(target_date)
    except Exception as e:
        print(f"  ✗ M.W lookup failed: {e}", file=sys.stderr)
        return 1

    # Read points from row 3
    inc = d.get("cumulative_increment")
    if inc:
        cur = excel.read("1n+", col, row=week_row)
        old = 0
        try:
            old = int(float(cur.get("value", 0) or 0))
        except (TypeError, ValueError):
            old = 0
        points = old + inc
        excel.write("1n+", col, row=week_row, value=str(points))
    else:
        row3 = excel.read("1n+", col, row=3)
        try:
            points = int(float(row3.get("value", 0) or 0))
        except (TypeError, ValueError):
            points = 0
        excel.write("1n+", col, row=week_row, value=str(points))

    # Append cell ref to 0分
    if fen_col:
        excel.append("0分", fen_col, date=target_date, value=f"+'1n+'!{col}{week_row}")

    # Close 1neon Todoist
    closed = _find_and_close_todoist(d.get("todoist_label") or "1neon", name, d.get("aliases", []))

    _append_completed(name)
    _fire_refresh()
    print(f"  ✓ {name} → 1n+!{col}{week_row} ({points} pts), 0分!{fen_col} appended"
          + (" + todoist closed" if closed else ""))
    return 0


_USER_ANNOT_RE = re.compile(r"\[(\d+)\]|\{(\d+)\}")


def _extract_annots(s: str) -> tuple[Optional[int], Optional[int]]:
    """Return ([N], {N}) integer annotations from `s`, or (None, None)."""
    sq, cu = None, None
    for m in _USER_ANNOT_RE.finditer(s):
        if m.group(1):
            sq = int(m.group(1))
        elif m.group(2):
            cu = int(m.group(2))
    return sq, cu


def _build_order_annots(content: str) -> tuple[Optional[int], Optional[int]]:
    """Find the matching line in build order, extract its [N]/{N}."""
    from neon.blocks import BUILD_ORDER, _bare
    if not BUILD_ORDER.exists():
        return None, None
    target_bare = _bare(content.strip())
    for line in BUILD_ORDER.read_text().splitlines():
        if "- [" not in line:
            continue
        body = line.split("- [", 1)[1].split("]", 1)[-1].strip() if "]" in line else ""
        if body == content.strip() or _bare(body) == target_bare:
            return _extract_annots(line)
    return None, None


def _domain_from_labels(labels: list) -> Optional[str]:
    """First label that matches a known domain code in the registry."""
    for lbl in labels:
        if registry.get_domain(lbl):
            return lbl
    return None


def run_one_off(query: str, target_date: str, raw_input: str) -> int:
    """Step 5: word-overlap match against ALL open Todoist tasks (any label).

    Resolves points via priority chain: user-typed → build-order → Todoist content → 0.
    Routes points to 0分 column based on matched task's domain label.
    Closes Todoist + flips build-order checkbox if 关键径路-labeled.
    """
    # Fetch all open tasks (paginated, no label filter)
    candidates = todoist.find_tasks(limit=500)
    best, best_score = None, 0.0
    for t in candidates:
        score = _word_overlap(query, t.get("content", ""))
        if score > best_score:
            best, best_score = t, score
    if not best or best_score < 0.6:
        return 2  # no match — fall through to agent for Step 6

    content = best.get("content", "")
    labels = best.get("labels", [])

    # Points priority: user → build order → Todoist content → 0
    user_sq, user_cu = _extract_annots(raw_input)
    bo_sq, bo_cu = (None, None)
    if "关键径路" in labels or "#0g" in labels or "#-1g" in labels:
        bo_sq, bo_cu = _build_order_annots(content)
    td_sq, td_cu = _extract_annots(content)

    points_sq = user_sq if user_sq is not None else (bo_sq if bo_sq is not None else td_sq)
    points_cu = user_cu if user_cu is not None else (bo_cu if bo_cu is not None else td_cu)

    domain = _domain_from_labels(labels)
    fen_writes = []
    if points_sq:
        if not domain:
            print(f"  ✗ matched task has no domain label; can't route [{points_sq}]: {content!r}",
                  file=sys.stderr)
            return 1
        fen_col = registry.resolve_fen_col(domain)
        excel.append("0分", fen_col, date=target_date, value=f"+{points_sq}")
        fen_writes.append(f"+{points_sq} → {fen_col}({domain})")
    if points_cu:
        from neon import cols
        ac_col = cols.col("0分", "0g")
        excel.append("0分", ac_col, date=target_date, value=f"+{points_cu}")
        fen_writes.append(f"+{points_cu} → {ac_col}(0g)")

    # Close Todoist
    try:
        todoist.close_task(best["id"])
        _drop_from_queue(best["id"])
    except Exception as e:
        print(f"  ✗ Todoist close failed: {e}", file=sys.stderr)
        return 1

    # Build-order checkbox flip
    flipped = False
    if "关键径路" in labels or "#0g" in labels or "#-1g" in labels:
        from neon.blocks import flip_checkbox
        flipped = flip_checkbox(content)

    _append_completed(content[:50])
    _fire_refresh()
    msg = f"  ✓ {content!r} (overlap={best_score:.2f})"
    if fen_writes:
        msg += " " + ", ".join(fen_writes)
    if flipped:
        msg += " + build-order ✓"
    print(msg)
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: run.py <input>", file=sys.stderr)
        return 64
    raw = " ".join(sys.argv[1:])
    items = re.split(r"\s*[,;]\s*", raw)
    rc = 0
    for item in items:
        if not item.strip():
            continue
        query, target, time_range, explicit_minutes = _parse_input(item)
        d = _route(query, target)
        d["query_after_strip"] = d.get("query_after_strip", query)
        step = d.get("step")
        if step == "0n":
            rc |= run_0n(d, item, target, time_range, explicit_minutes)
        elif step == "1n+":
            rc |= run_1n(d, target)
        else:
            # Step 5: try one-off Todoist match before deferring to agent
            sub_rc = run_one_off(d.get("query_after_strip") or query, target, item)
            if sub_rc == 2:
                print(f"  → step={step} for {item!r}: no Todoist match; deferring to /did agent (Step 6)",
                      file=sys.stderr)
            rc |= sub_rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
