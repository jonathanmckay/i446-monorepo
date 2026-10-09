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
  alerts         z_ibx/alerts.jsonl entries from the last 7 days, grouped

Auto-fixes (only ever these): trigger a Syncthing rescan on a folder that is
out of sync. Everything else is surfaced, never changed.

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
TASK_CONTENT = f"{AUTO_MARK} 1-i446 vault health review (20) [15]"
TASK_LABELS = ["i447"]
TASK_KEY = "1-i446 vault health review"

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


def check_time_machine() -> Finding:
    detail, status, review = [], "ok", False
    s, d, r = _tm(lambda c: sh(c, shell=True)); detail += [f"{socket.gethostname()}: {x}" for x in d]; status, review = s, r
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


# ---------------------------------------------------------------------------
# Report + task
# ---------------------------------------------------------------------------
def run_all(now: dt.datetime) -> list[Finding]:
    out = []
    for fn in (check_singletons, check_duplicate_notes, lambda: check_syncthing(now), lambda: check_git_autopush(now),
               lambda: check_skills_backup(now), lambda: check_onedrive_neon(now), check_time_machine,
               check_ix_cron, lambda: check_alerts(now)):
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
             f"Weekly vault-health run on {socket.gethostname()}. Rules: singletons, duplicate notes, Syncthing, git autopush, "
             "skills backup, OneDrive Neon, Time Machine, alerts. Review items are in the 😈 Todoist task.", "",
             "| Check | Status | Summary | Auto-fixed |", "|---|---|---|---|"]
    for f in findings:
        lines.append(f"| {f.check} | {ICON[f.status]} {f.status} | {f.summary} | {f.fixed or ''} |")
    for f in findings:
        if f.detail:
            lines += ["", f"## {f.check}"] + [f"- {d}" for d in f.detail]
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
    existing = next((t for t in items if isinstance(t, dict) and TASK_KEY in t.get("content", "")), None)
    if not any(f.review for f in findings):
        if existing:
            td.close_task(existing["id"]); return f"all clear; closed task {existing['id']}"
        return "all clear; no task"
    desc = build_description(findings, report_rel)
    if existing:
        td._api("POST", f"/tasks/{existing['id']}", {"description": desc, "due_date": today.isoformat()})
        return f"updated task {existing['id']}"
    t = td._api("POST", "/tasks", {"content": TASK_CONTENT, "labels": TASK_LABELS, "due_date": today.isoformat(),
                                   "description": desc, "duration": 20, "duration_unit": "minute"})
    return f"created task {t.get('id') if isinstance(t, dict) else '?'}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
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
