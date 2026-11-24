#!/usr/bin/env python3
"""Estimate ledger: a retrospective row for every closed task (JM 2026-11-23).

Each close records what the task said it would cost and earn against what it
did: `(N)` estimated minutes of JM's attention vs his Toggl minutes on it, and
`[N]` on the task vs the 分 actually credited. Agents read it back with
`suggest` before they write an `(N)` on a new task.

Two writers, one row kind each, merged by task id + date on read:

- `close`  did-fast, on every Todoist close (dtd, /did, /fin). Spawned in the
           background (`record` below) because the Toggl lookup takes a second.
- `fin`    done_session, on /fin only: what the session knows and a plain
           close cannot (wall clock, model minutes, JM's turns).

One append-only file per host in the synced vault, so two hosts never write
the same file: ~/vault/g245/v_logs/estimate-ledger.<host>.jsonl

    estimate_ledger.py suggest "<task text>" [--domain i9]
    estimate_ledger.py record '<json list of closes>'      (did-fast only)
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import socket
import statistics
import sys
import time
from pathlib import Path

LOG_DIR = Path.home() / "vault" / "g245" / "v_logs"
MONO = Path.home() / "i446-monorepo"
ANNOT_RE = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")     # same as fin_report
MIN_RE = re.compile(r"\((\d+)\)")                        # same as dtd_groups
FEN_RE = re.compile(r"\[(\d+)\]")
TOKEN_RE = re.compile(r"(?<!\S)[@#]\S+")                 # @code / #tag: Toggl keeps them off the description
LOOKBACK_DAYS = 7
STOP = {"a", "an", "the", "to", "for", "of", "and", "in", "on", "with", "my", "at", "is"}


def host() -> str:
    """Short host name (`ix`, not `ix-local`), as the other v_logs files use."""
    short = socket.gethostname().split(".")[0]
    return re.sub(r"[^A-Za-z0-9]+", "-", short).strip("-")[:24] or "host"


def path(log_dir: Path | None = None) -> Path:
    return (log_dir or LOG_DIR) / f"estimate-ledger.{host()}.jsonl"


def clean_name(content: str) -> str:
    """Task name without (N) [N] {N}, 😈, @code and #tag."""
    s = ANNOT_RE.sub("", (content or "").replace("😈", ""))
    return " ".join(TOKEN_RE.sub("", s).split())


def key(s: str) -> str:
    """Match key shared by a task and its Toggl entries. tg strips `@code` and
    `#tag` from the description, so they must come off the task side too or a
    task named `x @i447` never matches its own timer."""
    return clean_name(s).lower()


def est_minutes(content: str) -> int | None:
    m = MIN_RE.search(content or "")
    return int(m.group(1)) if m else None


def est_points(content: str) -> int | None:
    vals = [int(v) for v in FEN_RE.findall(content or "")]
    return sum(vals) if vals else None


def domain_of(labels, content: str = "", known=None) -> str | None:
    """First label that is a domain code; else the first `@code` in the name."""
    known = known if known is not None else _domains()
    for lab in labels or []:
        if lab in known:
            return lab
    for tok in re.findall(r"(?<!\S)@(\S+)", content or ""):
        if tok in known:
            return tok
    return None


def _domains() -> set[str]:
    try:
        sys.path.insert(0, str(MONO / "mcp"))
        from toggl_server.config import PROJECT_MAP
        return set(PROJECT_MAP)
    except Exception:  # noqa: BLE001
        return set()


def jm_minutes(entries, content: str, now: float | None = None) -> tuple[int, int]:
    """(minutes, entries) of Toggl time whose description is this task."""
    now = now or time.time()
    k = key(content)
    secs = n = 0
    for e in entries or []:
        if not k or key(e.get("description") or "") != k:
            continue
        d = int(e.get("duration") or 0)
        if d < 0:   # running: Toggl stores -start_epoch
            try:
                st = _dt.datetime.fromisoformat(str(e.get("start")).replace("Z", "+00:00")).timestamp()
                d = max(0, int(now - st))
            except (ValueError, TypeError):
                d = 0
        secs += d
        n += 1
    return round(secs / 60), n


def toggl_entries(days: int = LOOKBACK_DAYS) -> list[dict]:
    try:
        # Through toggl_cli, as fin_report does: importing it loads the API key.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "toggl_cli_for_ledger", MONO / "mcp" / "toggl_server" / "toggl_cli.py")
        tc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tc)
        today = _dt.date.today()
        return tc.toggl_api.get_entries(start_date=(today - _dt.timedelta(days=days)).isoformat(),
                                     end_date=(today + _dt.timedelta(days=1)).isoformat()) or []
    except Exception:  # noqa: BLE001 — a retrospective never fails a close
        return []


def append(row: dict, log_dir: Path | None = None) -> bool:
    try:
        p = path(log_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def close_row(c: dict, entries, now: float | None = None) -> dict:
    """One `close` row from did-fast's payload {id, content, labels, points}."""
    now = now or time.time()
    content = c.get("content") or ""
    mins, n = jm_minutes(entries, content, now)
    return {
        "kind": "close",
        "ts": int(now),
        "date": _dt.datetime.fromtimestamp(now).date().isoformat(),
        "id": str(c.get("id") or ""),
        "task": clean_name(content),
        "domain": domain_of(c.get("labels"), content),
        "grp": "grp" in (c.get("labels") or []),
        "est_min": est_minutes(content),
        "jm_min": mins,
        "jm_entries": n,
        "est_fen": est_points(content),
        "fen": c.get("points"),
        "host": host(),
    }


def fin_row(tid: str, content: str, report: dict, now: float | None = None) -> dict:
    """The /fin half: session facts from fin_report.build()."""
    now = now or time.time()
    model = report.get("model") or {}
    jm = report.get("jm") or {}
    return {
        "kind": "fin",
        "ts": int(now),
        "date": _dt.datetime.fromtimestamp(now).date().isoformat(),
        "id": str(tid),
        "task": clean_name(content),
        "est_min": est_minutes(content),
        "jm_min": jm.get("minutes"),
        "wall_min": report.get("wall_minutes"),
        "model_min": model.get("active_minutes"),
        "turns": model.get("turns"),
        "host": host(),
    }


def record_fin(tid: str, content: str, report: dict | None, log_dir: Path | None = None) -> bool:
    """Called by done_session after a close. Never raises."""
    try:
        if not isinstance(report, dict) or report.get("error"):
            return False
        return append(fin_row(tid, content, report), log_dir)
    except Exception:  # noqa: BLE001
        return False


def record_closes(closes: list[dict], log_dir: Path | None = None, entries=None) -> int:
    entries = toggl_entries() if entries is None else entries
    return sum(append(close_row(c, entries), log_dir) for c in closes if c.get("id"))


# ── reading ───────────────────────────────────────────────────────────────

def load(log_dir: Path | None = None) -> list[dict]:
    """Every host's rows, merged to one record per (task id, date)."""
    merged: dict[tuple, dict] = {}
    for p in sorted((log_dir or LOG_DIR).glob("estimate-ledger.*.jsonl")):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            k = (r.get("id"), r.get("date"))
            cur = merged.setdefault(k, {})
            jm = max(cur.get("jm_min") or 0, r.get("jm_min") or 0)
            cur.update({f: v for f, v in r.items() if v is not None and f != "kind"})
            cur["jm_min"] = jm
    return list(merged.values())


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"\w+", clean_name(s).lower()) if w not in STOP}


def _stats(rows: list[dict]) -> dict:
    """Calibration over rows that carry an estimate. `n_zero` counts rows with
    no Toggl time: either no timer ran or JM spent nothing, so they are shown
    beside the ratio, not folded into it."""
    est = [r for r in rows if (r.get("est_min") or 0) > 0]
    timed = [r for r in est if (r.get("jm_min") or 0) > 0]
    ratios = [r["jm_min"] / r["est_min"] for r in timed]
    return {
        "n": len(est),
        "n_timed": len(timed),
        "n_zero": len(est) - len(timed),
        "median_actual_over_estimate": round(statistics.median(ratios), 2) if ratios else None,
    }


def suggest(text: str, domain: str | None = None, log_dir: Path | None = None, top: int = 5) -> dict:
    rows = load(log_dir)
    want = _words(text)
    scored = []
    for r in rows:
        have = _words(r.get("task") or "")
        overlap = len(want & have) / len(want) if want else 0
        if overlap > 0:
            scored.append((overlap, r))
    scored.sort(key=lambda x: (-x[0], -(x[1].get("ts") or 0)))
    out = {
        "all": _stats(rows),
        "similar": [{"task": r.get("task"), "date": r.get("date"), "domain": r.get("domain"),
                     "est_min": r.get("est_min"), "jm_min": r.get("jm_min"),
                     "wall_min": r.get("wall_min"), "est_fen": r.get("est_fen"), "fen": r.get("fen"),
                     "match": round(s, 2)} for s, r in scored[:top]],
    }
    if domain:
        out["domain"] = {"code": domain, **_stats([r for r in rows if r.get("domain") == domain])}
    return out


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "record":
        try:
            closes = json.loads(argv[1])
        except ValueError:
            return 1
        record_closes(closes if isinstance(closes, list) else [])
        return 0
    if len(argv) >= 2 and argv[0] == "suggest":
        dom = argv[argv.index("--domain") + 1] if "--domain" in argv[:-1] else None
        print(json.dumps(suggest(argv[1], dom), ensure_ascii=False, indent=2))
        return 0
    sys.stderr.write(__doc__ or "")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
