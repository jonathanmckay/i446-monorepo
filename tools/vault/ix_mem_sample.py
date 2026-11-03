#!/usr/bin/python3
"""ix_mem_sample.py — Ix memory sampler for the /1-i446 ix-memory check.

Every 15 min (config/ix.crontab) appends one JSON line to ~/Library/Logs/ix-mem.jsonl:
boot time, swap, free %, Chrome physical footprint (top's MEM, which counts
compressed/swapped pages that ps RSS drops), and every Chrome-family *browser*
main process (pid + start time + user-data-dir). A changed main pid/start is a
Chrome restart; two at once is an extra instance. 15-min sampling misses
browsers that live only a few minutes; that is accepted.

Added 2026-11-03 after two JetsamEvents on 10-29 (Chrome renderers at 12.8 GB,
then 23.6 GB, on a 16 GB Mac) and a Chrome restart at 11-02 03:00 that a
reboot-only check would never see.

Usage:
    ix_mem_sample.py              # append one sample (cron)
    ix_mem_sample.py --summary    # print a 7-day JSON summary (vault_health reads this over ssh)
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

LOG = Path.home() / "Library/Logs/ix-mem.jsonl"
DIAG = Path("/Library/Logs/DiagnosticReports")
KEEP_DAYS = 30
# Browser mains: the exe, not a helper, and no --type= (helpers all carry one).
BROWSER_EXE = re.compile(r"^(/.*?/Contents/MacOS/(?:Google Chrome|Google Chrome for Testing|Chromium)|\S*chrome-headless-shell)(?=\s|$)")
# Profiles our own automation uses on purpose: reported, not alarmed.
AUTOMATION_DIRS = ("playwright_", "appfolio_browser_state", "ms-playwright")


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except Exception:  # noqa: BLE001
        return ""


def _mb(s: str) -> float:
    m = re.match(r"([\d.]+)([BKMG])", s)
    if not m:
        return 0.0
    return float(m.group(1)) * {"B": 1 / 2**20, "K": 1 / 1024, "M": 1, "G": 1024}[m.group(2)]


def parse_browsers(ps_out: str) -> list[dict]:
    """`ps -axo pid=,ppid=,lstart=,args=` → Chrome-family browser main processes."""
    out = []
    for line in ps_out.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\w{3} \w{3}\s+\d+ [\d:]{8} \d{4})\s+(.*)", line)
        if not m:
            continue
        args = m.group(4)
        exe = BROWSER_EXE.match(args)
        if not exe or "--type=" in args:
            continue
        udd = re.search(r"--user-data-dir=(\S+)", args)
        out.append({"pid": int(m.group(1)), "ppid": int(m.group(2)), "start": m.group(3),
                    "exe": exe.group(1).rsplit("/", 1)[-1], "udd": udd.group(1) if udd else "default"})
    return out


def parse_top(top_out: str) -> tuple[float, int, list[list]]:
    """`top -l 1 -stats pid,mem,command` → (Chrome MB, Chrome proc count, top-5 [name, MB] by name)."""
    agg: dict[str, float] = {}
    chrome_mb, chrome_n = 0.0, 0
    for line in top_out.splitlines():
        m = re.match(r"\s*\d+\s+([\d.]+[BKMG])\+?-?\s+(.+?)\s*$", line)
        if not m:
            continue
        name, mb = m.group(2), _mb(m.group(1))
        key = "Google Chrome" if name.startswith("Google Chrome") else name
        agg[key] = agg.get(key, 0.0) + mb
        if key == "Google Chrome":
            chrome_mb += mb; chrome_n += 1
    top5 = sorted(agg.items(), key=lambda kv: -kv[1])[:5]
    return round(chrome_mb), chrome_n, [[k, round(v)] for k, v in top5]


def sample(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now().astimezone()
    boot = re.search(r"sec = (\d+)", _run(["/usr/sbin/sysctl", "-n", "kern.boottime"]))
    swap = re.search(r"total = ([\d.]+)M\s+used = ([\d.]+)M", _run(["/usr/sbin/sysctl", "-n", "vm.swapusage"]))
    free = re.search(r"free percentage: (\d+)%", _run(["/usr/bin/memory_pressure", "-Q"]))
    chrome_mb, chrome_n, top5 = parse_top(_run(["/usr/bin/top", "-l", "1", "-stats", "pid,mem,command"]))
    return {
        "ts": now.isoformat(timespec="seconds"),
        "boot": int(boot.group(1)) if boot else None,
        "swap_mb": round(float(swap.group(2))) if swap else None,
        "swap_total_mb": round(float(swap.group(1))) if swap else None,
        "free_pct": int(free.group(1)) if free else None,
        "chrome_mb": chrome_mb, "chrome_procs": chrome_n,
        "browsers": parse_browsers(_run(["/bin/ps", "-axo", "pid=,ppid=,lstart=,args="])),
        "top": top5,
    }


def append(s: dict, log: Path = LOG) -> None:
    lines = log.read_text().splitlines() if log.exists() else []
    cutoff = (dt.datetime.fromisoformat(s["ts"]) - dt.timedelta(days=KEEP_DAYS)).isoformat()
    lines = [l for l in lines if l[8:33] >= cutoff] + [json.dumps(s, ensure_ascii=False)]
    log.write_text("\n".join(lines) + "\n")


def _jetsam(path: Path) -> dict:
    """Largest process group in a JetsamEvent .ips (JSON after the first line)."""
    try:
        d = json.loads(path.read_text().split("\n", 1)[1])
    except Exception:  # noqa: BLE001
        return {"file": path.name, "top": "unreadable"}
    page = (d.get("memoryStatus") or {}).get("pageSize") or 16384
    agg: dict[str, list] = {}
    for p in d.get("processes", []):
        a = agg.setdefault(p.get("name", "?"), [0.0, 0]); a[0] += p.get("rpages", 0) * page / 2**30; a[1] += 1
    name, (gb, n) = max(agg.items(), key=lambda kv: kv[1][0]) if agg else ("?", (0.0, 0))
    return {"file": path.name, "top": f"{name} {gb:.1f} GB ×{n}", "largest": d.get("largestProcess")}


def summarize(now: dt.datetime, log: Path = LOG, diag: Path = DIAG, days: int = 7) -> dict:
    cutoff = now - dt.timedelta(days=days)
    samples = []
    for l in (log.read_text().splitlines() if log.exists() else []):
        try:
            s = json.loads(l)
            if dt.datetime.fromisoformat(s["ts"]) >= cutoff:
                samples.append(s)
        except Exception:  # noqa: BLE001
            continue
    live = re.search(r"sec = (\d+)", _run(["/usr/sbin/sysctl", "-n", "kern.boottime"]))
    boots = sorted({s["boot"] for s in samples if s.get("boot")} | ({int(live.group(1))} if live else set()))
    # Chrome restarts: the default-profile browser main pid/start changed between samples.
    restarts, extra, automation, prev = [], {}, {}, None
    for s in samples:
        bs = s.get("browsers", [])
        mains = [b for b in bs if b["udd"] == "default" and b["exe"] == "Google Chrome"]
        ident = (mains[0]["pid"], mains[0]["start"]) if mains else None
        if prev and ident and ident != prev:
            restarts.append(mains[0]["start"])
        prev = ident or prev
        for b in bs:
            if b["udd"] == "default" and b["exe"] == "Google Chrome":
                continue
            bucket = automation if any(k in b["udd"] for k in AUTOMATION_DIRS) else extra
            k = f"{b['exe']} udd={b['udd']}"
            e = bucket.setdefault(k, {"n": 0, "first": s["ts"], "ppid": b["ppid"]}); e["n"] += 1
    reports = []
    if diag.exists():
        for p in sorted(diag.iterdir()):
            try:
                mt = dt.datetime.fromtimestamp(p.stat().st_mtime).astimezone()
            except OSError:
                continue
            if mt < cutoff:
                continue
            if p.name.startswith("JetsamEvent-"):
                reports.append({"kind": "jetsam", "at": mt.isoformat(timespec="minutes"), **_jetsam(p)})
            elif p.suffix == ".panic" or p.name.startswith("Kernel-"):
                reports.append({"kind": "panic", "at": mt.isoformat(timespec="minutes"), "file": p.name})
            elif p.name.startswith("Google Chrome"):
                reports.append({"kind": "chrome", "at": mt.isoformat(timespec="minutes"), "file": p.name})
    # The live main's own start time catches a restart before the log saw the old pid.
    if prev and not restarts:
        try:
            started = dt.datetime.strptime(" ".join(prev[1].split()), "%a %b %d %H:%M:%S %Y").astimezone()
            if started >= cutoff:
                restarts.append(prev[1])
        except ValueError:
            pass
    peak = max(samples, key=lambda s: s.get("chrome_mb") or 0, default={})
    swaps = [s["swap_mb"] / s["swap_total_mb"] for s in samples if s.get("swap_mb") is not None and s.get("swap_total_mb")]
    return {
        "samples": len(samples), "last": samples[-1]["ts"] if samples else None,
        "boots_in_window": [b for b in boots if dt.datetime.fromtimestamp(b).astimezone() >= cutoff],
        "chrome_restarts": restarts, "extra_browsers": extra, "automation_browsers": automation,
        "chrome_peak_mb": peak.get("chrome_mb"), "chrome_peak_at": peak.get("ts"),
        "swap_peak_pct": round(100 * max(swaps)) if swaps else None,
        "top": samples[-1].get("top") if samples else None,
        "reports": reports,
    }


def main() -> int:
    now = dt.datetime.now().astimezone()
    if "--summary" in sys.argv:
        print(json.dumps(summarize(now), ensure_ascii=False))
        return 0
    append(sample(now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
