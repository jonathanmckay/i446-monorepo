#!/usr/bin/env python3
"""neon-task-checksum.py — verify every Neon habit column has its Todoist card.

The Neon sheets are the source of truth; Todoist cards are derived state that
occasionally rots (sync hiccup, accidental delete, a recurrence that failed to
roll forward — 2026-07-26: "1st hci" was simply gone and nothing noticed for
weeks because the only checker lived inside the dead -2n loop). This script
checks BOTH cadences and, with --fix, recreates what's missing:

  daily  (0n)  — the canonical set in config/daily-todoist-manifest.json,
                 checked/recreated via validate-daily-habits.py's logic
                 (imported), plus a manifest→0n-header drift check: every
                 manifest `match` must still be a live 0n header.
  weekly (1n+) — derived STRAIGHT from the sheet, no second manifest: every
                 header column with a day-of-week in row 3 must have an open
                 recurring 1neon task whose bare name matches the header
                 (did-fast aliases respected). Row 2 = (time), row 5 = [pts]
                 (a rate formula like "1/m" / ".5/m" / "15+1/m" means variable
                 points → the card gets no [N]). Wrong-weekday recurrences and
                 a present card whose [N] no longer matches row 5 are WARNED
                 about, never auto-corrected — the fix (card vs. sheet) is a
                 human decision ("2 "-prefixed headers are monthly by the
                 time-order notation and exempt from the weekday check).

Runs on IX (Excel lives there; hostname Jonathans-Mac-mini*) via local
osascript; anywhere else it transparently wraps the read through `ssh ix` so
the same script is testable from Straylight. Launchd: com.jm.neon-task-checksum
(daily 04:15 on Ix). Anything missing/recreated/drifted appends to
~/vault/z_ibx/alerts.jsonl (Syncthing carries it back to Straylight) and the
full report lands in ~/.cache/jm/neon-task-checksum.json.

NA note: dtd's ctrl-x same-day NA file lives on Straylight and is not visible
here; at the 04:15 run time it can't exist yet for the new day, and deleted-
yesterday cards SHOULD come back today, so the daily path simply skips NA
handling (validate-daily-habits still honors it when run from Straylight).

Usage:
    neon-task-checksum.py                 # report only (JSON)
    neon-task-checksum.py --fix           # recreate missing cards
    neon-task-checksum.py --pretty        # human-readable summary
    neon-task-checksum.py --weekly-only / --daily-only
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API = "https://api.todoist.com/api/v1"
WORKBOOK = "Neon分v12.2.xlsx"
IX_HOSTNAME_PREFIX = "Jonathans-Mac-mini"
ALERTS = Path.home() / "vault/z_ibx/alerts.jsonl"
REPORT = Path.home() / ".cache/jm/neon-task-checksum.json"
PROJECT_1NEON = "6Crfmq5PmPjqrx4x"  # same Habits project the daily cards use

# Mirror of did-fast.py's ONENEON_ALIASES (checksum runs on Ix where importing
# did-fast is not safe; test_neon_task_checksum.py cross-checks the two on
# Straylight so they cannot drift silently).
ALIASES = {
    "1 hcbp": "1 hcb",
    "家": "family",
    "relax": "relax {60}",
    "一起吃": "一起饭",
    "long o314": "长o314",
    "1 groceries": "groceries",
    "1 i447": "i447",
}

# 1=Sunday … 7=Saturday (1n+ row 3; verified against the live cards 2026-07-25)
DAY_NAMES = ["sunday", "monday", "tuesday", "wednesday",
             "thursday", "friday", "saturday"]

# Domain label per 1n+ header (from the 2026-07-25 audit). Unknown → 1neon only.
WEEKLY_DOMAIN = {
    "1s": "g245", "1g": "g245", "1 hpm": "hcm", "s+hcbp": "hcbp",
    "1 f692": "m5x2", "1 f693": "i9", "1 m5x2": "m5x2", "1 i9": "i9",
    "1 -2g": "g245", "1 vm+li+msgr": "i9", "1 -1n": "g245", "1 f694": "f694",
    "1 xk88": "xk88", "1 xk87": "xk87", "1 xk87 wknd": "xk87", "1 cal": "g245",
    "1 s897": "s897", "1 hcm": "hcmc", "1 hcmc": "hcmc", "1 hcb": "hcb", "长o314": "hcm",
    "groceries": "hcb", "1 hcme": "hcm", "1 sunset": "hcm", "2 hci": "hci",
    "长冥想": "hcm", "业写": "h335", "一起饭": "xk87", "nails": "hci",
    "aos": "xk88", "family": "家", "s897": "s897", "relax {60}": "hcm",
    "i447": "i447", "1 对身": "hcb", "1 f695": "m5x2", "1 kids nature": "xk87",
}


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def norm_name(s: str) -> str:
    """Bare habit name: strip 😈, (N)/[N]/{N}, [x/m] rate markers; collapse
    whitespace; lowercase. Card "AoS (15) [15]" and header "aos" both → "aos";
    card "relax (60)" and header "relax {60}" both → "relax"."""
    s = s.lstrip("😈").strip()
    s = re.sub(r"\s*\[[0-9.+]*/m\]", "", s)
    s = re.sub(r"\s*[\[\(\{][^\]\)\}]*[\]\)\}]", "", s)
    return re.sub(r"[\s\-—–]+", " ", s).strip().lower()


def _num(v):
    """AppleScript cell → number or None ('45.0' → 45, '' → None, '1/m' → None)."""
    try:
        f = float(str(v).strip())
        return int(f) if f == int(f) else f
    except (ValueError, TypeError):
        return None


def parse_1n_expectations(row1, row2, row3, row5) -> list[dict]:
    """Rows 1/2/3/5 of 1n+ (parallel lists) → expected weekly habits.
    A column participates only when it has a header AND a day 1-7 in row 3
    (drops the col-2 label block and the ∑/avg tail)."""
    out = []
    for h, t, d, p in zip(row1, row2, row3, row5):
        header = str(h).strip() if h is not None else ""
        day = _num(d)
        if not header or day not in (1, 2, 3, 4, 5, 6, 7):
            continue
        pts = _num(p)
        out.append({
            "header": header,
            "time": _num(t),
            "day": int(day),
            "pts": pts,                      # None → variable (rate formula)
            "rate": None if pts is not None else str(p).strip(),
        })
    return out


def match_weekly(expected: list[dict], open_contents: list[str]):
    """Split expectations into (present, missing) by bare-name match against
    the open 1neon card contents. Aliases map card-side names to headers."""
    norm_to_header = {}
    for e in expected:
        norm_to_header[norm_name(e["header"])] = e["header"]
    present_norms = set()
    for c in open_contents:
        n = norm_name(c)
        n = norm_name(ALIASES.get(n, n))
        present_norms.add(n)
    present, missing = [], []
    for e in expected:
        (present if norm_name(e["header"]) in present_norms else missing).append(e)
    return present, missing


def recreate_guard(kind: str, fetched_count: int, expected_count: int,
                   missing_count: int) -> str | None:
    """Why this run's --fix recreate pass must be SKIPPED, or None when
    recreation is safe.

    A rate-limited Todoist intermittently returns 200-with-empty (and
    sometimes a partial page) — did-fast.py guards this exact class in its
    cache refresh (did-fast.py keep-old guards, 2026-07-26), but the
    checksum didn't: an empty/partial 1neon fetch declared the whole sheet
    "missing" and --fix recreated it wholesale (2026-07-25 23:38Z: 13 cards
    in 7 seconds; daily strays every run after — user report 2026-07-28).
    The legitimate case this script exists for is 1-2 rotted cards, so a
    run where EVERYTHING (empty fetch) or a large fraction (>2 and >30%)
    reads as missing is an API flake, not mass deletion — report + alert,
    never recreate."""
    if expected_count and fetched_count == 0:
        return f"{kind} fetch returned 0 open tasks — rate-limit flake suspected"
    if missing_count > 2 and missing_count > 0.3 * expected_count:
        return (f"{missing_count}/{expected_count} {kind} habits 'missing' in "
                "one run — API flake suspected, not mass deletion")
    return None


def find_duplicates(open_contents: list[str]) -> list[str]:
    """Normalized names carried by MORE than one open card. The checksum
    could never see duplicates (any one match counts a habit present), so
    recreation-burst leftovers accumulated silently. Warn-only."""
    counts: dict[str, int] = {}
    for c in open_contents:
        n = norm_name(c)
        n = norm_name(ALIASES.get(n, n))
        if n:
            counts[n] = counts.get(n, 0) + 1
    return sorted(n for n, k in counts.items() if k >= 2)


def weekday_warnings(expected: list[dict], tasks: list[dict]) -> list[str]:
    """Warn when a card's recurrence day contradicts the sheet's row-3 day.
    tasks: [{content, due_string}]. "2 "-prefixed headers are monthly-notation
    and exempt. Warn-only — the fix is a human decision (AoS case)."""
    by_norm = {}
    for t in tasks:
        n = norm_name(t.get("content", ""))
        n = norm_name(ALIASES.get(n, n))
        by_norm.setdefault(n, []).append(t.get("due_string") or "")
    warnings = []
    for e in expected:
        if e["header"].startswith("2 "):
            continue
        want = DAY_NAMES[e["day"] - 1]
        for ds in by_norm.get(norm_name(e["header"]), []):
            dsl = ds.lower()
            if any(d in dsl for d in DAY_NAMES) and want not in dsl:
                warnings.append(
                    f"{e['header']}: sheet says {want} (row 3 = {e['day']}), "
                    f"card recurs '{ds}'")
    return warnings


def points_mismatches(expected: list[dict], tasks: list[dict]) -> list[str]:
    """Warn when a present card's [N] doesn't match the sheet's row-5
    expected points. Variable-rate columns (row 5 = a rate formula, e['pts']
    is None) are skipped — their points are computed from duration at
    completion, not fixed. Warn-only, same as weekday_warnings: a mismatch
    could mean either side is stale, so the fix is a human decision
    (regression 2026-07-28: '1 xk87' sheet said [45], card silently drifted
    to [20] weeks earlier and nothing noticed — existence-only matching
    never compares the bracketed number)."""
    by_norm: dict[str, list[str]] = {}
    for t in tasks:
        n = norm_name(t.get("content", ""))
        n = norm_name(ALIASES.get(n, n))
        by_norm.setdefault(n, []).append(t.get("content", ""))
    warnings = []
    for e in expected:
        if e["pts"] is None:
            continue
        for content in by_norm.get(norm_name(e["header"]), []):
            m = re.search(r"\[(\d+(?:\.\d+)?)\]", content)
            if not m:
                continue
            card_pts = float(m.group(1))
            if card_pts != e["pts"]:
                warnings.append(
                    f"{e['header']}: sheet expects [{e['pts']:g}], "
                    f"card has [{card_pts:g}] ({content!r})")
    return warnings


def weekly_create_payload(exp: dict) -> dict:
    """Todoist create body for a missing weekly habit. Card name = header with
    any {N} stripped (a literal {N} in a card name triggers the 0g-bonus write
    on completion — the "relax (60)" precedent). Variable-rate columns get no
    [N]; points are computed from minutes at completion."""
    name = re.sub(r"\s*\{\d+\}", "", exp["header"]).strip()
    content = name
    if exp["time"] is not None:
        content += f" ({exp['time']})"
    if exp["pts"] is not None:
        content += f" [{exp['pts']}]"
    labels = ["1neon"]
    dom = WEEKLY_DOMAIN.get(exp["header"]) or WEEKLY_DOMAIN.get(exp["header"].lower())
    if dom:
        labels.append(dom)
    return {
        "content": content,
        "description": f"auto-recreated by neon-task-checksum {date.today().isoformat()}",
        "due_string": f"every {DAY_NAMES[exp['day'] - 1].capitalize()}",
        "labels": labels,
        "project_id": PROJECT_1NEON,
    }


def manifest_drift(manifest: dict, headers_0n: list[str]) -> list[str]:
    """Manifest habits whose `match` is no longer a live 0n header (renamed or
    removed column). Warn-only; the manifest is hand-curated."""
    live = {norm_name(h) for h in headers_0n}
    return [k for k, h in manifest["habits"].items()
            if norm_name(h["match"]) not in live]


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _on_ix() -> bool:
    return platform.node().startswith(IX_HOSTNAME_PREFIX)


def run_osascript(script: str) -> str:
    if _on_ix():
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=60)
    else:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "ix", "osascript", "-"],
                           input=script, capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        raise RuntimeError(f"osascript failed: {r.stderr.strip()[:300]}")
    return r.stdout


READ_SHEETS = f'''tell application "Microsoft Excel"
    set wb to workbook "{WORKBOOK}"
    set ws0 to sheet "0n" of wb
    set ws1 to sheet "1n+" of wb
    set out to ""
    set tmp0 to value of range "D1:BL1" of ws0
    set h0 to item 1 of tmp0
    repeat with v in h0
        if v is not missing value then set out to out & (v as text)
        set out to out & "\\t"
    end repeat
    set out to out & "\\n"
    repeat with rn in {{1, 2, 3, 5}}
        set tmpR to value of range ("C" & rn & ":AL" & rn) of ws1
        set rowVals to item 1 of tmpR
        repeat with v in rowVals
            if v is not missing value then set out to out & (v as text)
            set out to out & "\\t"
        end repeat
        set out to out & "\\n"
    end repeat
    return out
end tell'''


def read_sheets():
    """→ (headers_0n: list[str], rows_1n: [row1, row2, row3, row5])."""
    raw = run_osascript(READ_SHEETS)
    lines = raw.rstrip("\n").split("\n")
    headers_0n = [c for c in lines[0].split("\t") if c.strip()]
    rows = [ln.split("\t") for ln in lines[1:5]]
    return headers_0n, rows


def _token() -> str:
    import os
    tok = os.environ.get("TODOIST_API_KEY")
    if tok:
        return tok
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "todoist-api-key", "-w"],
            capture_output=True, text=True, timeout=5)
        if r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    cfg = json.loads((Path.home() / ".claude.json").read_text())
    auth = cfg["mcpServers"]["todoist"]["headers"]["Authorization"]
    return auth.split(None, 1)[1].strip()


def fetch_label_tasks(token: str, label: str) -> list[dict]:
    tasks, url = [], None
    q = urllib.parse.quote(f"@{label}")
    url = f"{API}/tasks/filter?query={q}&limit=200"
    while url:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        d = json.load(urllib.request.urlopen(req))
        for t in d.get("results", []):
            due = t.get("due") or {}
            tasks.append({"id": t["id"], "content": t["content"],
                          "due_string": due.get("string", "")})
        c = d.get("next_cursor")
        url = (f"{API}/tasks/filter?query={q}&limit=200&cursor="
               f"{urllib.parse.quote(c)}") if c else None
    return tasks


def create_task(token: str, body: dict) -> None:
    req = urllib.request.Request(
        f"{API}/tasks", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    urllib.request.urlopen(req)


def emit_alert(reason: str, detail: str) -> None:
    try:
        ALERTS.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERTS, "a") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "host": platform.node(), "tool": "neon-task-checksum",
                "severity": "warning", "reason": reason, "detail": detail,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _load_vdh():
    p = REPO / "scripts" / "validate-daily-habits.py"
    spec = importlib.util.spec_from_file_location("vdh", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--daily-only", action="store_true")
    ap.add_argument("--weekly-only", action="store_true")
    args = ap.parse_args()

    token = _token()
    report = {"date": date.today().isoformat(), "host": platform.node()}
    headers_0n, rows_1n = read_sheets()

    if not args.weekly_only:
        vdh = _load_vdh()
        manifest = json.loads(vdh.MANIFEST.read_text())
        daily_tasks = fetch_label_tasks(token, "0neon") + fetch_label_tasks(token, "夜neon")
        missing = vdh.compute_missing(manifest, [t["content"] for t in daily_tasks])
        # Habits the user deleted from dtd TODAY (ctrl-x -> explicit 0 + NA
        # marker) must not be resurrected same-day — validate-daily-habits.py's
        # own main() already honors this; this script re-implements the same
        # missing/recreate logic and had skipped the check entirely, so its
        # --fix pass (and the 04:15 launchd run) silently recreated habits the
        # user had just deleted (regression 2026-07-28: "cleared out 1st hci
        # from dtd... but I see it here again").
        na = vdh.na_today()
        skipped_na = [k for k in missing if vdh.bare(manifest["habits"][k]["match"]) in na]
        missing = [k for k in missing if k not in skipped_na]
        recreated = []
        skip_daily = recreate_guard("daily", len(daily_tasks),
                                    len(manifest["habits"]), len(missing))
        if skip_daily:
            emit_alert("checksum_recreate_skipped", skip_daily)
            report.setdefault("recreate_skipped", {})["daily"] = skip_daily
        if args.fix and not skip_daily:
            for key in missing:
                try:
                    create_task(token, vdh.recreate_payload(manifest["habits"][key]))
                    recreated.append(key)
                except Exception as e:
                    report.setdefault("errors", []).append(f"daily {key}: {e}")
        drift = manifest_drift(manifest, headers_0n)
        report["daily"] = {"checked": len(manifest["habits"]), "missing": missing,
                           "recreated": recreated, "manifest_drift": drift,
                           "skipped_na": skipped_na}
        if missing:
            emit_alert("daily_habit_missing",
                       f"{', '.join(missing)}" + (" (recreated)" if recreated else ""))
        if drift:
            emit_alert("manifest_drift",
                       f"manifest match not in 0n headers: {', '.join(drift)}")

    if not args.daily_only:
        expected = parse_1n_expectations(*rows_1n)
        weekly_tasks = fetch_label_tasks(token, "1neon")
        contents = [t["content"] for t in weekly_tasks]
        present, missing_w = match_weekly(expected, contents)
        warnings = weekday_warnings(expected, weekly_tasks)
        pts_warnings = points_mismatches(expected, weekly_tasks)
        dupes = find_duplicates(contents)
        recreated_w = []
        skip_weekly = recreate_guard("weekly", len(weekly_tasks),
                                     len(expected), len(missing_w))
        if skip_weekly:
            emit_alert("checksum_recreate_skipped", skip_weekly)
            report.setdefault("recreate_skipped", {})["weekly"] = skip_weekly
        if args.fix and not skip_weekly:
            for e in missing_w:
                try:
                    create_task(token, weekly_create_payload(e))
                    recreated_w.append(e["header"])
                except Exception as ex:
                    report.setdefault("errors", []).append(f"weekly {e['header']}: {ex}")
        report["weekly"] = {
            "checked": len(expected), "present": len(present),
            "missing": [e["header"] for e in missing_w],
            "recreated": recreated_w, "weekday_warnings": warnings,
            "points_mismatches": pts_warnings,
            "duplicates": dupes,
        }
        if dupes:
            emit_alert("weekly_habit_duplicate", ", ".join(dupes))
        if missing_w:
            emit_alert("weekly_habit_missing",
                       f"{', '.join(e['header'] for e in missing_w)}"
                       + (" (recreated)" if recreated_w else ""))
        for w in warnings:
            emit_alert("weekly_weekday_mismatch", w)
        for w in pts_warnings:
            emit_alert("weekly_points_mismatch", w)

    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    except OSError:
        pass

    if args.pretty:
        d, w = report.get("daily", {}), report.get("weekly", {})
        print(f"daily:  {d.get('checked', '-')} checked, "
              f"missing: {d.get('missing') or 'none'}, drift: {d.get('manifest_drift') or 'none'}")
        print(f"weekly: {w.get('checked', '-')} checked, "
              f"missing: {w.get('missing') or 'none'}")
        for warn in w.get("weekday_warnings", []):
            print(f"  ⚠ {warn}")
        for warn in w.get("points_mismatches", []):
            print(f"  ⚠ {warn}")
    else:
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
