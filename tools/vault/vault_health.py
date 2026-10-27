#!/usr/bin/env python3
"""vault_health.py — the /1-i446 weekly vault-health review.

Runs a set of rules over the vault and the machinery that keeps it alive,
auto-fixes what is safe to fix, writes a dated report to z_meta/, and upserts
ONE 😈 Todoist task whose description lists only the items that need JM's
decision (dtd's agent gesture injects that description into the Claude
prompt, so starting the task opens the review with the list in hand).

Checks (v1, 2026-09-27):
  singletons     folders under the ~3-doc rule (tools/vault/singleton-audit.py scan)
  duplicate-notes one note name held by 2+ live notes outside dated folders,
                 with the notes that bare-link it (added 2026-10-09 after
                 hcmc/epcn.md and hcmp/epcn.md diverged unnoticed)
  syncthing      device connectivity, folder state, remote completion, errors
  git-autopush   ~/vault and ~/i446-monorepo: stuck rebase, stale commit,
                 unpushed commits, autopush log warnings
  skills-backup  ~/.claude/skills local git auto-commit freshness
  onedrive-neon  Neon workbook mtime (and Ix↔Straylight parity when run off Ix)
  time-machine   destination configured + latest backup age
  ix-cron        Ix's live crontab matches the committed config/ix.crontab
  skill-claims   skills whose name matches a recurring Todoist card but that
                 aren't linked to dtd (agent_claims.SKILL_CLAIMS) or recorded
                 as declined (SKILL_NO_CLAIM) (added 2026-10-11)
  alerts         z_ibx/alerts.jsonl entries from the last 7 days, grouped
  duplicates     Downloads duplicates queued by tools/triage (byte-identical
                 `name (1).ext` copies, JPGs beside their HEIC), on this host
                 (added 2026-10-27)

Auto-fixes (only ever these): trigger a Syncthing rescan on a folder that is
out of sync; trash the queued Downloads duplicates after re-verifying each
against its original (not on --dry-run). Everything else is surfaced, never
changed.

Usage:
    vault_health.py            # run, write report, upsert task
    vault_health.py --dry-run  # run and print, no report/task
    vault_health.py --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
VAULT = Path.home() / "vault"
MONO = Path.home() / "i446-monorepo"
REPORT_DIR = VAULT / "z_meta"
ALERTS = VAULT / "z_ibx/alerts.jsonl"
NEON = Path.home() / "OneDrive/vault-excel/Neon分v12.2.xlsx"
SKILLS = Path.home() / ".claude/skills"
ST_CONFIG = Path.home() / "Library/Application Support/Syncthing/config.xml"
ST_URL = "http://127.0.0.1:8384"
AUTO_MARK = "😈"
# 2026-10-17: the review is the recurring weekly card '1 i446' (every Saturday, i447 project),
# not a one-off filed each run. The run only refreshes its description; JM completes it in
# dtd for the [15], so the script never closes it.
TASK_CONTENT = "1 i446 (20) [15]"
TASK_LABELS = ["i447"]
TASK_KEY = "1 i446"
TASK_PROJECT = "6Crfmq5PmPjqrx4x"   # i447

APPLY = True   # main() clears it for --dry-run / --json so no auto-fix deletes anything

_SA = importlib.util.spec_from_file_location("singleton_audit", Path(__file__).with_name("singleton-audit.py"))
singleton_audit = importlib.util.module_from_spec(_SA); sys.modules["singleton_audit"] = singleton_audit
_SA.loader.exec_module(singleton_audit)


@dataclass
class Finding:
    check: str
    status: str            # ok | warn | fail
    summary: str
    detail: list = field(default_factory=list)
    review: bool = False   # needs JM's decision → goes into the Todoist task
    fixed: str | None = None


def on_ix() -> bool:
    return socket.gethostname().lower().startswith("ix")


def sh(cmd: list[str] | str, timeout: float = 30.0, shell: bool = False) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=shell)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {e}"


def ix(cmd: str, timeout: float = 30.0) -> str:
    """Run a shell command on Ix (no-op passthrough when already there)."""
    if on_ix():
        return sh(cmd, timeout, shell=True)
    return sh(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "ix", cmd], timeout)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_singletons() -> Finding:
    rows = singleton_audit.scan()
    if not rows:
        return Finding("singletons", "ok", "no folders under the ~3-doc rule")
    detail = [f"{r['rel']}/ — {', '.join(r['files']) or 'note only'}" for r in rows]
    return Finding("singletons", "warn", f"{len(rows)} folder(s) under the ~3-doc rule", detail, review=True)


WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|[^\]]*)?\]\]")
# Note names that legitimately repeat. Grow this from /1-i446 answers, the way
# singleton-audit.py's EXCLUDE_PREFIXES records confirmed exceptions.
DUP_NAMES_OK = {
    "claude",       # per-folder CLAUDE.md instruction files, one per tree by design
    "portfolio",    # h335/m5x2/fund-*/portfolio/portfolio.md, one folder note per fund
    # 2026-10-11 /1-i446 answers:
    "m405", "c313", "rl16", "w225",  # property note vs its portfolio-sales-narrative section
    "reviews",      # h335/f693/reviews (performance) vs hcmc/reviews (books), both folder notes
    "older",        # h335/m5x2/r202/**/older/ archive-bucket folder notes
}


def _skip_dir(rel: str) -> bool:
    """Same exclusions as the singleton audit (archives, mirrors, transcripts)."""
    parts = rel.split("/")
    if any(p in singleton_audit.EXCLUDE_NAMES or p.startswith(".") for p in parts):
        return True
    return any(rel == e or rel.startswith(e.rstrip("/") + "/") for e in singleton_audit.EXCLUDE_PREFIXES)


def _dated(rel: str) -> bool:
    return any(singleton_audit.DATE_DIR_RE.match(p) for p in rel.split("/")[:-1])


def scan_duplicate_notes(vault: Path = VAULT) -> dict[str, dict]:
    """{name: {"paths": [rel...], "linked_from": [rel...]}} for every note
    name held by 2+ live notes outside dated folders (o314/2019/life.md vs
    o314/2020/life.md is a journal, not a conflict). linked_from lists notes
    with a bare [[name]] link, i.e. the ones Obsidian resolves by guessing."""
    notes: dict[str, list[str]] = {}
    for root, dirs, files in os.walk(vault):
        rel = os.path.relpath(root, vault)
        rel = "" if rel == "." else rel
        if rel and _skip_dir(rel):
            dirs[:] = []
            continue
        for f in files:
            if f.endswith(".md"):
                notes.setdefault(f[:-3].lower(), []).append(f"{rel}/{f}" if rel else f)
    out = {}
    for k, paths in notes.items():
        live = sorted(p for p in paths if not _dated(p))
        if len(live) > 1 and k not in DUP_NAMES_OK:
            out[k] = {"paths": live, "linked_from": []}
    if not out:
        return out
    for paths in notes.values():
        for src in paths:
            try:
                text = (vault / src).read_text(errors="ignore")
            except OSError:
                continue
            for m in WIKILINK_RE.finditer(text):
                name = m.group(1).strip()
                key = (name[:-3] if name.lower().endswith(".md") else name).lower()
                if "/" not in name and key in out and src not in out[key]["linked_from"]:
                    out[key]["linked_from"].append(src)
    return out


def check_duplicate_notes() -> Finding:
    dups = scan_duplicate_notes()
    if not dups:
        return Finding("duplicate-notes", "ok", "no note name is shared by two live notes")
    detail = []
    for k, v in sorted(dups.items(), key=lambda kv: (-len(kv[1]["linked_from"]), kv[0])):
        lf = sorted(v["linked_from"])
        tail = (f" — [[{k}]] from {', '.join(lf[:3])}" + (f" +{len(lf) - 3}" if len(lf) > 3 else "")) if lf else ""
        detail.append(f"{k}: {' | '.join(v['paths'])}{tail}")
    return Finding("duplicate-notes", "warn",
                   f"{len(dups)} note name(s) held by 2+ live notes (merge, rename, or confirm as OK)",
                   detail, review=True)


# ---------------------------------------------------------------------------
# Vault-wide style (2026-10-21, JM: "add a vault-wide style check to 1-i446").
# Rules from z_meta.md › Style Guide › Filenames / Titles:
#   filename   ASCII kebab-case (code first; scripts live in title/aliases)
#   arabic     no Arabic in titles (RTL breaks sorting, links and the TUIs)
#   translated no bilingual titles: English OR Chinese, not both
#   long       titles in as few characters as possible (> STYLE_TITLE_MAX)
# Excluded: archives/mirrors (_skip_dir), dated journal folders, and d359
# (people notes are Title Case names by design). Grow STYLE_OK from /1-i446
# answers the way DUP_NAMES_OK does.
# ---------------------------------------------------------------------------
STYLE_TITLE_MAX = 40
STYLE_SKIP_TOP = {"d359"}
STYLE_OK: set[str] = {               # relative paths confirmed as exceptions
    # Script-written (2026-10-24 style sweep): h335/stats is generated by monorepo tools.
    "h335/stats/2026-02-m5x2-0-II-analysis.md", "h335/stats/2026.02-r202-COGS-analysis.md",
    "h335/stats/m5x2m_detailed_report.md", "h335/stats/m5x2-jan-2026-visualizations.md",
    "h335/stats/r202-pnl-analysis.md", "h335/stats/r203-comprehensive-analysis.md",
    # i447/i446 is exempt from vault styling (transcripts, monorepo docs).
    "i447/i446/README-session-tracking.md", "i447/i446/claude-code-permissions.md",
    "i447/i446/i446-workiq-workflows.md",
    # Monthly narrative docs: tools/1g/narrative_month.py writes "YYYY.MM 2g+1g-narrative.md" and
    # 1g_review.py plus the 1g/1s/1i9/1m5x2 skills read that name; rename generator + readers together.
    "g245/5e2/2026.07 2g-narrative.md", "g245/5e2/2026.08 2g-narrative.md",
    "g245/5e2/2026.09 2g+1g-narrative.md", "g245/5e2/2026.10 2g+1g-narrative.md",
    "m5x2/STYLE.md",                 # meta-docs standard: README/STYLE/TODO/DECISIONS are uppercase
    # Book short titles that are 41-42 characters; cutting them loses the title.
    "hcmc/reviews/undated/harry-potter-and-the-order-of-the-phoenix.md",
    "hcmc/reviews/undated/la-reine-dans-le-palais-des-courants-dair.md",
    "hcmc/reviews/undated/zen-and-the-art-of-motorcycle-maintenance-an-inquiry-into-values.md",
}
STYLE_FILENAME_OK = {"CLAUDE.md", "CONTEXT.md"}   # instruction files are uppercase by design
_KEBAB_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]*$")
_ARABIC_RE = re.compile(r"[؀-ۿ]")
_CJK_RE = re.compile(r"[一-鿿]")
_CODE_RE = re.compile(r"^[a-z]+\d+[a-z0-9+]*$|^\d+[a-z]+$")   # h335, m5x2, 0g: codes, not words
_TITLE_RE = re.compile("^title:\\s*[\"']?(.*?)[\"']?\\s*$", re.M)
STYLE_RULES = ("filename", "date", "arabic", "translated", "long")
# Dates in filenames are YYYY-MM-DD (periods YYYY-MM, YYYY-qN) (2026-10-24).
# Flags the shapes old imports produced: 20180731, 2-92018 / 2-9-2018, 2022q1,
# and month-first periods (08-2024).
_BAD_DATE_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:[0-2]\d|3[01])(?!\d)"
                          r"|(?<!\d)\d{1,2}-\d{1,2}-?(?:19|20)\d{2}(?!\d)"
                          r"|(?<!\d)(?:19|20)\d{2}q[1-4]"
                          r"|(?<![\d-])(?:0[1-9]|1[0-2])-(?:19|20)\d{2}(?!\d)"   # 08-2024 (MM-YYYY)
                          r"|(?<!\d)(?<!\d-)(?:0[1-9]|1[0-2])-(?:19|20)\d{2}(?!\d)")


def _latin_words(title: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z][A-Za-z'&-]*", title) if not _CODE_RE.match(w.lower())]


def scan_style(vault: Path = VAULT) -> dict[str, list[tuple[str, str]]]:
    """{rule: [(rel_path, title), ...]} for every live note off the style guide."""
    out: dict[str, list[tuple[str, str]]] = {r: [] for r in STYLE_RULES}
    for root, dirs, files in os.walk(vault):
        rel = os.path.relpath(root, vault)
        rel = "" if rel == "." else rel
        if rel and (_skip_dir(rel) or rel.split("/")[0] in STYLE_SKIP_TOP or _dated(rel + "/x")):
            dirs[:] = []
            continue
        for f in sorted(files):
            if not f.endswith(".md"):
                continue
            r = f"{rel}/{f}" if rel else f
            if r in STYLE_OK:
                continue
            try:
                head = (Path(root) / f).read_text(errors="ignore")[:800]
            except OSError:
                continue
            m = _TITLE_RE.search(head)
            title = m.group(1).strip() if m else ""
            if not _KEBAB_RE.match(f[:-3]) and f not in STYLE_FILENAME_OK:
                out["filename"].append((r, title))
            if _BAD_DATE_RE.search(f[:-3]):
                out["date"].append((r, title))
            if _ARABIC_RE.search(title):
                out["arabic"].append((r, title))
            elif _CJK_RE.search(title) and len(_latin_words(title)) >= 2:
                out["translated"].append((r, title))
            if len(title) > STYLE_TITLE_MAX:
                out["long"].append((r, title))
    return out


ROOT_FILES_OK = {"CLAUDE.md"}   # plus dotfiles (git, Syncthing, Obsidian config)


def check_root(vault: Path | None = None) -> Finding:
    """Vault root holds only folders, CLAUDE.md and dotfiles (style guide
    2026-10-24: notes belong in their domain, junk in the trash). The 10-24
    cleanup found 10 Syncthing conflict copies of an old log (658 MB), empty
    stub notes, a probe file and two real notes stranded at the top."""
    vault = vault or VAULT
    stray = sorted(e.name for e in vault.iterdir()
                   if e.is_file() and not e.name.startswith(".") and e.name not in ROOT_FILES_OK)
    if not stray:
        return Finding("vault-root", "ok", "only folders and CLAUDE.md at the top level")
    return Finding("vault-root", "warn", f"{len(stray)} stray file(s) at the vault root: move notes to their "
                   "domain, trash the rest", stray[:20], review=True)


def check_style() -> Finding:
    found = scan_style(VAULT)   # looked up at call time, so tests can repoint VAULT
    total = sum(len(v) for v in found.values())
    if not total:
        return Finding("style", "ok", "every live note is on the style guide (filename, title)")
    counts = ", ".join(f"{len(found[r])} {r}" for r in STYLE_RULES if found[r])
    detail = []
    for r in STYLE_RULES:
        rows = found[r]
        if not rows:
            continue
        by_top: dict[str, int] = {}
        for rel, _t in rows:
            by_top[rel.split("/")[0]] = by_top.get(rel.split("/")[0], 0) + 1
        folders = ", ".join(f"{k} {v}" for k, v in sorted(by_top.items(), key=lambda kv: -kv[1])[:6])
        detail.append(f"{r}: {len(rows)} — {folders}")
        sample = sorted(rows, key=lambda x: -len(x[1]))[:5] if r == "long" else rows[:5]
        detail += [f"  {rel}" + (f" — {t[:60]}" if t and r != "filename" else "") for rel, t in sample]
    return Finding("style", "warn", f"{total} note(s) off the style guide ({counts}); fix in batches or add to STYLE_OK",
                   detail, review=True)


# ---------------------------------------------------------------------------
# Toggl entries store (2026-10-21, JM: "make sure it has dates from the last
# week"). ~/vault/i156/toggl.sqlite is written nightly on Ix and copied here
# by Syncthing; every weekly review reads it instead of the API, so a stale
# or gappy store silently understates the reviews. Checks: the file exists,
# the last completed sync is recent, every one of the last 7 full days has
# entries, no window in the last 14 days was marked incomplete, and the
# dashboard export is as fresh as the store.
# ---------------------------------------------------------------------------
TOGGL_STORE = VAULT / "i156" / "toggl.sqlite"
TOGGL_EXPORT = MONO / "tools" / "personal-dashboard" / ".toggl-daily-cache.json"
TOGGL_STORE_MAX_AGE_H = 36       # the 05:10 sync, plus a day of slack for Syncthing


def scan_toggl_store(now: dt.datetime, store: Path = TOGGL_STORE, export: Path = TOGGL_EXPORT) -> dict:
    """Facts about the store for check_toggl_store: {ok: bool, problems: [..], facts: [..]}."""
    import sqlite3
    out = {"ok": True, "problems": [], "facts": []}
    if not store.exists():
        out["ok"] = False; out["problems"].append(f"missing: {store} (run toggl_db.py backfill on Ix)")
        return out
    try:
        conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
        n, lo, hi = conn.execute("SELECT COUNT(*), MIN(day), MAX(day) FROM entries").fetchone()
        last = conn.execute("SELECT MAX(fetched_at) FROM sync_log WHERE complete = 1").fetchone()[0]
        today = now.astimezone(TZ).date()
        week = [(today - dt.timedelta(days=i)).isoformat() for i in range(1, 8)]
        have = {r[0] for r in conn.execute("SELECT DISTINCT day FROM entries WHERE day BETWEEN ? AND ?", (week[-1], week[0]))}
        bad_windows = conn.execute("SELECT COUNT(*) FROM sync_log WHERE complete = 0 AND window_end >= ?",
                                   ((today - dt.timedelta(days=14)).isoformat(),)).fetchone()[0]
        conn.close()
    except sqlite3.Error as e:
        out["ok"] = False; out["problems"].append(f"unreadable: {e}")
        return out
    out["facts"].append(f"{n} entries, {lo}..{hi}")
    if not last:
        out["ok"] = False; out["problems"].append("no completed sync recorded")
    else:
        age_h = (now.timestamp() - last) / 3600
        when = dt.datetime.fromtimestamp(last, TZ).strftime("%Y-%m-%d %H:%M")
        out["facts"].append(f"last completed sync {when} ({age_h:.0f} h ago)")
        if age_h > TOGGL_STORE_MAX_AGE_H:
            out["ok"] = False; out["problems"].append(f"last sync is {age_h:.0f} h old (cron on Ix / Syncthing?)")
    missing = [d for d in week if d not in have]
    if missing:
        out["ok"] = False; out["problems"].append("no entries on " + ", ".join(sorted(missing)))
    else:
        out["facts"].append("all of the last 7 days have entries")
    if bad_windows:
        out["ok"] = False; out["problems"].append(f"{bad_windows} incomplete sync window(s) in the last 14 days (short API responses)")
    try:
        exp_hi = max(json.loads(export.read_text()))
        if exp_hi < (hi or ""):
            out["ok"] = False; out["problems"].append(f"dashboard export ends {exp_hi}, store ends {hi} (export-cache not run)")
    except (OSError, ValueError):
        out["problems"].append(f"dashboard export unreadable: {export}")
        out["ok"] = False
    return out


def check_toggl_store(now: dt.datetime) -> Finding:
    r = scan_toggl_store(now, TOGGL_STORE, TOGGL_EXPORT)   # looked up at call time (tests repoint them)
    if r["ok"]:
        return Finding("toggl-store", "ok", "; ".join(r["facts"]))
    return Finding("toggl-store", "fail", "; ".join(r["problems"]), r["facts"] + r["problems"], review=True)


def _st_get(path: str, key: str):
    req = urllib.request.Request(f"{ST_URL}{path}", headers={"X-API-Key": key})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def _st_post(path: str, key: str) -> None:
    req = urllib.request.Request(f"{ST_URL}{path}", headers={"X-API-Key": key}, method="POST", data=b"")
    urllib.request.urlopen(req, timeout=5).read()


def check_syncthing(now: dt.datetime) -> Finding:
    if not ST_CONFIG.exists():
        return Finding("syncthing", "fail", "no Syncthing config on this host", review=True)
    cfg = ST_CONFIG.read_text()
    key = re.search(r"<apikey>([^<]+)</apikey>", cfg)
    if not key:
        return Finding("syncthing", "fail", "no API key in Syncthing config", review=True)
    key = key.group(1)
    folders = re.findall(r'<folder id="([^"]+)" label="([^"]*)" path="([^"]+)"', cfg)
    devices = {m[0]: m[1] for m in re.findall(r'<device id="([^"]+)" name="([^"]*)"', cfg)}
    detail, status, review, fixed = [], "ok", False, []
    try:
        conns = _st_get("/rest/system/connections", key)["connections"]
        stats = _st_get("/rest/stats/device", key)
        my_id = _st_get("/rest/system/status", key)["myID"]
    except Exception as e:  # noqa: BLE001
        return Finding("syncthing", "fail", f"Syncthing API unreachable: {str(e)[:120]}", review=True)
    for dev, name in devices.items():
        if dev == my_id:
            continue
        connected = bool(conns.get(dev, {}).get("connected"))
        seen = stats.get(dev, {}).get("lastSeen", "")
        try:
            seen_dt = dt.datetime.fromisoformat(seen.replace("Z", "+00:00")).astimezone(TZ)
            days = (now - seen_dt).days
        except Exception:  # noqa: BLE001
            days = 9999
        if connected:
            detail.append(f"{name}: connected")
        elif days > 3:
            detail.append(f"{name}: NOT connected, last seen {days} days ago")
            status, review = "warn", True
        else:
            detail.append(f"{name}: not connected right now (seen {days}d ago)")
    for fid, label, _path in folders:
        if not fid:
            continue
        try:
            st = _st_get(f"/rest/db/status?folder={fid}", key)
        except Exception as e:  # noqa: BLE001
            detail.append(f"folder {label or fid}: status error {str(e)[:60]}"); status = "warn"; continue
        need = st.get("needBytes", 0) or 0
        errs = st.get("errors", 0) or 0
        state = st.get("state", "?")
        line = f"folder {label or fid}: {state}, need {need} B, errors {errs}"
        if errs:
            status, review = "fail", True
        elif need > 0 and state == "idle":
            # Safe auto-fix: ask Syncthing to rescan; nothing is deleted or moved.
            try:
                _st_post(f"/rest/db/scan?folder={fid}", key); fixed.append(f"rescan {label or fid}")
            except Exception:  # noqa: BLE001
                pass
            if status == "ok":
                status = "warn"
        detail.append(line)
        for dev, name in devices.items():
            if dev == my_id or not conns.get(dev, {}).get("connected"):
                continue
            try:
                comp = _st_get(f"/rest/db/completion?folder={fid}&device={dev}", key).get("completion", 100)
            except Exception:  # noqa: BLE001
                continue
            if comp < 99.5:
                detail.append(f"  {name} has {comp:.1f}% of {label or fid}")
                status = "warn" if status == "ok" else status
    summary = "all devices connected, folders idle" if status == "ok" else "see detail"
    return Finding("syncthing", status, summary, detail, review=review, fixed=", ".join(fixed) or None)


def _git_state(repo: Path, log: Path | None, now: dt.datetime, is_pusher: bool) -> tuple[str, list[str], bool]:
    detail, status, review = [], "ok", False
    if not repo.exists():
        return "warn", [f"{repo}: missing"], False
    g = repo / ".git"
    if (g / "rebase-merge").exists() or (g / "rebase-apply").exists():
        detail.append(f"{repo.name}: REBASE IN PROGRESS (autopush is stuck)"); return "fail", detail, True
    ts = sh(["git", "-C", str(repo), "log", "-1", "--format=%ct"]).strip()
    try:
        age_h = (now.timestamp() - int(ts)) / 3600
    except ValueError:
        age_h = 9999
    sb = sh(["git", "-C", str(repo), "status", "-sb"]).splitlines()[0] if sh(["git", "-C", str(repo), "status", "-sb"]) else ""
    ahead = re.search(r"ahead (\d+)", sb)
    detail.append(f"{repo.name}: last commit {age_h:.1f}h ago; {sb.strip()}")
    if is_pusher:
        if age_h > 24:
            status, review = "warn", True; detail.append(f"{repo.name}: no autopush commit in {age_h:.0f}h")
        if ahead and int(ahead.group(1)) > 0 and age_h > 1:
            status, review = "warn", True; detail.append(f"{repo.name}: {ahead.group(1)} unpushed commit(s)")
    if log and log.exists():
        tail = log.read_text().splitlines()[-3:]
        if any("WARNING" in l or "failed" in l.lower() for l in tail):
            status, review = ("fail" if status != "fail" else status), True
            detail.append(f"{repo.name}: autopush log tail: {' | '.join(t[:80] for t in tail)}")
    return status, detail, review


def check_git_autopush(now: dt.datetime) -> Finding:
    detail, status, review = [], "ok", False
    pairs = [(VAULT, Path.home() / "Library/Logs/vault-autopush.log"),
             (MONO, MONO / "scripts/.autopush.log")]
    if on_ix():
        for repo, log in pairs:
            s, d, r = _git_state(repo, log, now, is_pusher=True)
            detail += d; review |= r; status = max(status, s, key=["ok", "warn", "fail"].index)
    else:
        # Ix is the pusher; ask it. Local repos are informational only.
        for repo, log in pairs:
            s, d, r = _git_state(repo, None, now, is_pusher=False); detail += [f"(local) {x}" for x in d]
        out = ix("for r in ~/vault ~/i446-monorepo; do cd $r; "
                 "test -d .git/rebase-merge -o -d .git/rebase-apply && echo \"$r REBASE\"; "
                 "echo \"$r $(git log -1 --format=%ct) $(git status -sb | head -1)\"; done; "
                 "tail -2 ~/Library/Logs/vault-autopush.log; tail -2 ~/i446-monorepo/scripts/.autopush.log")
        for line in out.splitlines():
            if "REBASE" in line:
                status, review = "fail", True; detail.append(f"(ix) {line} — autopush stuck")
            elif line.startswith("/Users") and re.search(r" \d{9,} ", line):
                p, ts, *rest = line.split(" ", 2)
                age_h = (now.timestamp() - int(ts)) / 3600
                detail.append(f"(ix) {Path(p).name}: last commit {age_h:.1f}h ago; {' '.join(rest)}")
                if age_h > 24 or re.search(r"ahead \d+", " ".join(rest)):
                    status = "warn" if status == "ok" else status; review = True
            elif "WARNING" in line or "failed" in line.lower():
                status, review = "fail", True; detail.append(f"(ix log) {line[:100]}")
            elif line.startswith("["):
                detail.append(f"(ix log) {line[:100]}")
    return Finding("git-autopush", status, "healthy" if status == "ok" else "see detail", detail, review=review)


def check_skills_backup(now: dt.datetime) -> Finding:
    ts = ix("cd ~/.claude/skills && git log -1 --format=%ct").strip().splitlines()[-1] if True else ""
    try:
        age_h = (now.timestamp() - int(ts)) / 3600
    except ValueError:
        return Finding("skills-backup", "warn", f"could not read ~/.claude/skills git log on Ix: {ts[:80]}", review=True)
    if age_h > 24:
        return Finding("skills-backup", "warn", f"last auto-commit {age_h:.0f}h ago (cron is every 10 min)", review=True)
    return Finding("skills-backup", "ok", f"last auto-commit {age_h:.1f}h ago")


def check_perm_sync(now: dt.datetime) -> Finding:
    # claude-perm-sync.py (launchd on Ix, hourly) merges Claude permission
    # allowlists ix<->straylight and stamps this file only on full success.
    # The job runs on Ix, so only Ix has the stamp; off Ix, read Ix's
    # (2026-10-24: Straylight runs warned "never completed" every week).
    raw = ix("cat ~/.claude/perm-sync-last-success 2>/dev/null", 20).strip()
    if not re.fullmatch(r"[0-9]+(\.[0-9]+)?", raw):
        return Finding("perm-sync", "warn", "no last-success stamp on Ix — claude-perm-sync has never completed", review=True)
    age_h = (now.timestamp() - float(raw)) / 3600
    if age_h > 48:
        return Finding("perm-sync", "warn", f"last successful ix<->straylight permission sync {age_h:.0f}h ago "
                       "(hourly launchd job; is straylight offline or ssh broken?)", review=True)
    return Finding("perm-sync", "ok", f"last successful sync {age_h:.1f}h ago")


def check_onedrive_neon(now: dt.datetime) -> Finding:
    detail = []
    def mtime_local():
        return NEON.stat().st_mtime if NEON.exists() else None
    m_here = mtime_local()
    if m_here is None:
        return Finding("onedrive-neon", "fail", "Neon workbook not found in OneDrive on this host", review=True)
    age_h = (now.timestamp() - m_here) / 3600
    detail.append(f"{socket.gethostname()}: workbook modified {age_h:.1f}h ago")
    status, review = "ok", False
    if on_ix():
        if age_h > 72:
            status, review = "warn", True; detail.append("no writes in 3 days — is the daemon / Excel up?")
        return Finding("onedrive-neon", status, "see detail" if status != "ok" else "fresh", detail, review=review)
    out = ix("stat -f %m ~/OneDrive/vault-excel/Neon分v12.2.xlsx").strip().splitlines()[-1]
    try:
        m_ix = int(out)
    except ValueError:
        return Finding("onedrive-neon", "warn", f"could not stat Ix copy: {out[:80]}", detail, review=True)
    gap_h = (m_ix - m_here) / 3600
    detail.append(f"ix copy is {gap_h:+.1f}h relative to this copy")
    if abs(gap_h) > 24:
        status, review = "fail", True
        detail.append("OneDrive parity broken (>24h gap) — see reference_onedrive_neon_stale_sync: restart OneDrive, check fileproviderd")
    return Finding("onedrive-neon", status, "in sync" if status == "ok" else "parity gap", detail, review=review)


def _tm(host_cmd) -> tuple[str, list[str], bool]:
    dest = host_cmd("tmutil destinationinfo 2>&1 | head -3")
    if "No destinations configured" in dest:
        return "fail", ["no Time Machine destination configured"], True
    latest = host_cmd("tmutil latestbackup 2>&1 | tail -1").strip()
    m = re.search(r"(\d{4}-\d{2}-\d{2})-(\d{6})", latest)
    if not m:
        return "warn", [f"latest backup unreadable: {latest[:80]}"], True
    when = dt.datetime.strptime(m.group(1) + m.group(2), "%Y-%m-%d%H%M%S").replace(tzinfo=TZ)
    days = (dt.datetime.now(TZ) - when).days
    return ("warn" if days > 2 else "ok"), [f"latest backup {days}d ago"], days > 2


# Hosts that never get Time Machine. Straylight is a Microsoft-managed work
# Mac (JM 2026-10-11); its vault/monorepo copies are synced to Ix, which is
# the machine that backs up.
TM_EXEMPT_HOSTS = ("straylight",)


def check_time_machine() -> Finding:
    detail, status, review = [], "ok", False
    host = socket.gethostname()
    if host.lower().startswith(TM_EXEMPT_HOSTS):
        detail.append(f"{host}: exempt (work-managed Mac; Ix holds the backups)")
    else:
        s, d, r = _tm(lambda c: sh(c, shell=True)); detail += [f"{host}: {x}" for x in d]; status, review = s, r
    if not on_ix():
        s, d, r = _tm(lambda c: ix(c)); detail += [f"ix: {x}" for x in d]
        status = max(status, s, key=["ok", "warn", "fail"].index); review |= r
    return Finding("time-machine", status, "ok" if status == "ok" else "no or stale backups", detail, review=review)


def check_ix_cron() -> Finding:
    """Ix's live crontab must match config/ix.crontab (the committed copy).
    2026-09-27: a failed remote rewrite left the crontab EMPTY for minutes;
    without a parity check that would have gone unnoticed until Monday."""
    ref = MONO / "config/ix.crontab"
    if not ref.exists():
        return Finding("ix-cron", "warn", "config/ix.crontab missing", review=True)
    live = ix("crontab -l 2>/dev/null")
    want = [l for l in ref.read_text().splitlines() if l.strip() and not l.startswith("#")]
    have = [l for l in live.splitlines() if l.strip() and not l.startswith("#")]
    missing = [l for l in want if l not in have]
    extra = [l for l in have if l not in want]
    if not have:
        return Finding("ix-cron", "fail", "Ix crontab is EMPTY — restore with: crontab config/ix.crontab",
                       [f"{len(want)} jobs expected"], review=True)
    if missing or extra:
        return Finding("ix-cron", "warn", f"{len(missing)} job(s) missing, {len(extra)} extra vs config/ix.crontab",
                       [f"missing: {m[:90]}" for m in missing] + [f"extra: {e[:90]}" for e in extra], review=True)
    return Finding("ix-cron", "ok", f"{len(have)} jobs match config/ix.crontab")


_CARD_ANNOT = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")   # did-fast's ANNOT_RE


def _norm(s: str) -> str:
    return re.sub(r"[\s_]+", "", s.lower())


def _agent_claims():
    spec = importlib.util.spec_from_file_location("agent_claims_for_health", MONO / "tools/did/agent_claims.py")
    mod = importlib.util.module_from_spec(spec); sys.modules["agent_claims_for_health"] = mod
    spec.loader.exec_module(mod); return mod


def _recurring_cards() -> list[str]:
    sys.path.insert(0, str(MONO / "lib"))
    import todoist
    return [t.get("content", "") for t in todoist.find_tasks(limit=5000)
            if (t.get("due") or {}).get("is_recurring")]


def unlinked_skill_cards(skill_names, cards, linked, declined) -> list[tuple[str, str]]:
    """(skill, card) pairs where a skill's name matches a recurring card by
    name ('1i9' ~ '1 i9', 'moralaudit' ~ 'moral audit', 'f695' ~ '1 f695')
    and the skill is neither linked nor declined. Name matching is the only
    signal: grepping SKILL.md for did-fast calls flags /did's own examples."""
    by_norm: dict[str, str] = {}
    for c in cards:
        clean = " ".join(_CARD_ANNOT.sub("", c).split())
        by_norm.setdefault(_norm(clean), clean)
    out = []
    for s in sorted(skill_names):
        if s in linked or s in declined:
            continue
        for key in (_norm(s), "1" + _norm(s)):
            if key in by_norm:
                out.append((s, by_norm[key]))
    return out


def check_skill_claims(cards: list[str] | None = None) -> Finding:
    """New skills that look like they belong to a recurring card should claim
    it in dtd while they run (/d-style). JM links them in agent_claims.py, or
    records a no in SKILL_NO_CLAIM so this stops asking."""
    ac = _agent_claims()
    skills = [d.name for d in SKILLS.iterdir() if (d / "SKILL.md").exists()]
    pairs = unlinked_skill_cards(skills, _recurring_cards() if cards is None else cards,
                                 set(ac.SKILL_CLAIMS), set(ac.SKILL_NO_CLAIM))
    if not pairs:
        return Finding("skill-claims", "ok", f"{len(ac.SKILL_CLAIMS)} skills linked to dtd cards; no new candidates")
    return Finding("skill-claims", "warn", f"{len(pairs)} skill(s) may need a dtd card link",
                   [f"/{s} → '{c}': link in agent_claims.SKILL_CLAIMS (+ hook prefilter), "
                    "or decline in SKILL_NO_CLAIM" for s, c in pairs], review=True)


def check_alerts(now: dt.datetime) -> Finding:
    if not ALERTS.exists():
        return Finding("alerts", "ok", "no alerts sink")
    cutoff = now - dt.timedelta(days=7)
    groups: dict[tuple, dict] = {}
    for line in ALERTS.read_text().splitlines()[-2000:]:
        try:
            a = json.loads(line)
            ts = dt.datetime.fromisoformat(a.get("ts", "").replace("Z", "+00:00")).astimezone(TZ)
        except Exception:  # noqa: BLE001
            continue
        if ts < cutoff:
            continue
        k = (a.get("tool", "?"), a.get("reason", "?"), a.get("severity", "?"))
        g = groups.setdefault(k, {"n": 0, "last": ts, "detail": ""})
        g["n"] += 1
        if ts >= g["last"]:
            g["last"], g["detail"] = ts, str(a.get("detail", ""))[:120]
    if not groups:
        return Finding("alerts", "ok", "no alerts in the last 7 days")
    detail = [f"{k[0]} / {k[1]} [{k[2]}] ×{g['n']}, last {g['last']:%m-%d %H:%M}: {g['detail']}"
              for k, g in sorted(groups.items(), key=lambda kv: kv[1]["last"], reverse=True)]
    crit = any(k[2] in ("critical", "error") for k in groups)
    return Finding("alerts", "fail" if crit else "warn", f"{sum(g['n'] for g in groups.values())} alert(s) in 7 days",
                   detail, review=True)


NEON_DIFF_DIR = VAULT / "g245" / "neon-ledger" / "rules-diff"


def check_neon_rules(now: dt.datetime) -> Finding:
    """Neon off Excel step 3: is the nightly rule-engine parallel run alive,
    and which 0分 columns does the ledger still derive differently from Excel?
    JM asked for this to live here (2026-10-19) because he will not remember
    to check the diff himself; the Saturday 1 i446 card carries it."""
    files = sorted(NEON_DIFF_DIR.glob("20??-??-??.json")) if NEON_DIFF_DIR.exists() else []
    if not files:
        return Finding("neon-rules", "fail", "no rules-diff output yet (bin/run_neon_rules_diff.sh on Ix, cron 04:05)",
                       review=True)
    newest = dt.date.fromisoformat(files[-1].stem)
    age = (now.date() - newest).days
    detail, status, review = [], "ok", False
    if age > 2:
        status, review = "fail", True
        detail.append(f"newest diff is for {newest} ({age} days old) — the Ix 04:05 job is not running")
    mism: dict[str, int] = {}
    unknown: dict[str, int] = {}
    clean = 0
    for f in files[-7:]:
        try:
            d = json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            continue
        s = d.get("summary", {})
        for c in s.get("mismatch", []):
            mism[c] = mism.get(c, 0) + 1
        for u in s.get("unknown_inputs", []):
            unknown[u] = unknown.get(u, 0) + 1
        if not s.get("mismatch") and not s.get("partial"):
            clean += 1
    n = len(files[-7:])
    if mism:
        status = "fail" if status == "fail" else "warn"
        review = True
        top = ", ".join(f"{c}×{k}" for c, k in sorted(mism.items(), key=lambda kv: -kv[1]))
        detail.append(f"columns the ledger derives differently from Excel (days in last {n}): {top}")
        detail.append("decide per column: rulebook gap in lib/neon/rules.py, or an Excel-side edit the ledger never saw")
    if unknown:
        topu = ", ".join(f"{u}×{k}" for u, k in sorted(unknown.items(), key=lambda kv: -kv[1])[:8])
        detail.append(f"inputs the ledger has never observed (partial derivations): {topu}")
    summary = f"parallel run: {clean}/{n} day(s) fully clean, newest {newest}" + (f"; {len(mism)} column(s) mismatching" if mism else "")
    return Finding("neon-rules", status, summary, detail, review=review)


def _load_triage():
    spec = importlib.util.spec_from_file_location("triage", MONO / "tools/triage/triage.py")
    mod = importlib.util.module_from_spec(spec); sys.modules["triage"] = mod
    spec.loader.exec_module(mod)
    return mod


def check_duplicates() -> Finding:
    """Trash the duplicates tools/triage queued on this host (2026-10-27: JM wants
    duplicates surfaced and removed weekly here rather than daily and silently)."""
    triage = _load_triage()
    if not triage.DUPES.exists():
        return Finding("duplicates", "ok", f"no duplicate queue on {socket.gethostname().split('.')[0]}")
    good, dropped = triage.trash_queue(do_apply=APPLY)
    if not good and not dropped:
        return Finding("duplicates", "ok", "no queued duplicates")
    verb = "trashed" if APPLY else "would trash"
    mb = sum(r.get("size", 0) for r in good) / 1e6
    detail = [f"{verb}: {Path(r['path']).name} (same as {Path(r['original']).name})" for r in good]
    detail += [f"dropped (no longer a duplicate): {Path(r['path']).name}" for r in dropped]
    return Finding("duplicates", "ok", f"{verb} {len(good)} Downloads duplicate(s), {mb:.1f} MB", detail,
                   fixed=f"trashed {len(good)}" if APPLY and good else None)


# ---------------------------------------------------------------------------
# Report + task
# ---------------------------------------------------------------------------
def run_all(now: dt.datetime) -> list[Finding]:
    out = []
    for fn in (check_singletons, check_duplicate_notes, check_root, check_style, lambda: check_toggl_store(now), lambda: check_syncthing(now), lambda: check_git_autopush(now),
               lambda: check_skills_backup(now), lambda: check_perm_sync(now), lambda: check_onedrive_neon(now), check_time_machine,
               check_ix_cron, check_skill_claims, lambda: check_alerts(now), lambda: check_neon_rules(now),
               check_duplicates):
        try:
            out.append(fn())
        except Exception as e:  # noqa: BLE001 — one broken check must not hide the others
            name = getattr(fn, "__name__", "check").replace("check_", "").replace("<lambda>", "check")
            out.append(Finding(name, "fail", f"check crashed: {str(e)[:120]}", review=True))
    return out


ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌"}


def build_report(findings: list[Finding], today: dt.date) -> str:
    lines = ["---", f'title: "1-i446 Vault Health {today.isoformat()}"', f"date: {today.isoformat()}",
             "type: audit", "tags: [z_meta, audit, i446, i447]", "source: vault_health.py", "status: active", "---",
             f"Weekly vault-health run on {socket.gethostname()}. Rules: singletons, duplicate notes, style (filenames/titles), Toggl store, Syncthing, git autopush, "
             "skills backup, perm sync, OneDrive Neon, Time Machine, alerts, Neon rule-engine parallel run, Downloads duplicates. Review items are in the 😈 Todoist task.", "",
             "| # | Check | Status | Summary | Auto-fixed |", "|---|---|---|---|---|"]
    # Numbered rows (user request 2026-10-17: "number each check") so the
    # conversation can refer to "check 6" and the detail sections match.
    for n, f in enumerate(findings, 1):
        lines.append(f"| {n} | {f.check} | {ICON[f.status]} {f.status} | {f.summary} | {f.fixed or ''} |")
    for n, f in enumerate(findings, 1):
        if f.detail:
            lines += ["", f"## {n}. {f.check}"] + [f"- {d}" for d in f.detail]
    return "\n".join(lines) + "\n"


def build_description(findings: list[Finding], report_rel: str) -> str:
    items = [f for f in findings if f.review]
    head = [f"1-i446 weekly vault-health review. Report: {report_rel}",
            f"{len(items)} item(s) need a decision. Walk through them WITH me, numbered, one decision each. "
            "Don't change anything until I answer. Auto-fixes already applied are listed in the report.", ""]
    body = []
    n = 0
    for f in items:
        n += 1
        body.append(f"{n}. [{f.check}] {f.summary}")
        for d in f.detail[:12]:
            body.append(f"   - {d}")
        if len(f.detail) > 12:
            body.append(f"   - … {len(f.detail) - 12} more in the report")
    return "\n".join(head + body)[:15000]


def upsert_task(findings: list[Finding], report_rel: str, today: dt.date) -> str:
    td = singleton_audit._todoist()
    items = td._fetch_tasks(f"search: {TASK_KEY}")
    existing = next((t for t in items if isinstance(t, dict)
                     and t.get("content", "").lstrip(AUTO_MARK).strip().startswith(TASK_KEY)), None)
    clear = not any(f.review for f in findings)
    desc = f"all clear {today.isoformat()}. Report: {report_rel}" if clear else build_description(findings, report_rel)
    tag = " (all clear)" if clear else ""
    if existing:
        td._api("POST", f"/tasks/{existing['id']}", {"description": desc})
        return f"updated task {existing['id']}{tag}"
    t = td._api("POST", "/tasks", {"content": TASK_CONTENT, "labels": TASK_LABELS, "project_id": TASK_PROJECT,
                                   "due_string": "every Saturday", "description": desc,
                                   "duration": 20, "duration_unit": "minute"})
    return f"created recurring task {t.get('id') if isinstance(t, dict) else '?'}{tag}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    global APPLY
    APPLY = not (a.dry_run or a.json)
    now = dt.datetime.now(TZ); today = now.date()
    findings = run_all(now)
    if a.json:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2)); return 0
    report = build_report(findings, today)
    if a.dry_run:
        print(report); return 0
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"{today.isoformat()}-1-i446.md"
    path.write_text(report)
    rel = str(path.relative_to(VAULT))
    try:
        status = upsert_task(findings, rel, today)
    except Exception as e:  # noqa: BLE001
        status = f"todoist failed: {e}"
    worst = max((f.status for f in findings), key=["ok", "warn", "fail"].index)
    print(f"{today} 1-i446: {worst}; {sum(f.review for f in findings)} review item(s) → {rel}; {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
