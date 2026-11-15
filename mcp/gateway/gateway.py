#!/usr/bin/env python3
"""gateway.py: run each heavy MCP server once on Ix and share it over HTTP.

    gateway.py install              render com.jm.mcp-<name>.plist for every server in
                                    servers.json into ~/Library/LaunchAgents and (re)start it
    gateway.py status               one line per server: launchd pid, /status reply, child pids
    gateway.py claude-config        print the ~/.claude.json mcpServers entries that point at the gateway
    gateway.py claude-config --apply  back up ~/.claude.json and rewrite those entries in place
    gateway.py uninstall            bootout every agent (the plists stay; ~/.claude.json is untouched)

2026-11-14 ("Ix: cap MCP node servers piling up"): every Claude Code session starts
every user-scope MCP server, so 28 sessions meant 28 appfolio + 28 quickbooks + 28
google-calendar node processes and 28 workspace-mcp python processes, ~550 MB a
session before the claude process itself, and macOS jetsammed on 11-13 (node x135,
8.6 GB). Behind mcp-proxy (sparfenyuk) each stdio server runs once; the proxy keeps
one ClientSession to it and fans every HTTP client in. One agent per server, not one
gateway for all four: a child dying exits its proxy and KeepAlive restarts just that
one, and a child failing at startup cannot leave a sibling 404ing for days.

Secrets never enter this repo: env_file is sourced by sh before exec, and env values
may reference what it exported ($APPFOLIO_VHOST). The secrets dir is on the external
"Ix Files" volume, which TCC hides from launchd processes ("Operation not permitted"
from /bin/sh), so `install` stages a 0600 copy of each env_file under
~/.config/mcp-gateway/ and the agent sources that copy; re-run `install` after a
secret changes. Rollback: `gateway.py uninstall` and
`cp ~/.claude.json.bak-<stamp> ~/.claude.json`.
"""
from __future__ import annotations

import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
SERVERS = HERE / "servers.json"
AGENTS = HOME / "Library" / "LaunchAgents"
LOGS = HOME / "Library" / "Logs"
PROXY = HOME / ".local" / "bin" / "mcp-proxy"
STAGED = HOME / ".config" / "mcp-gateway"       # launchd-readable copies of the env files
PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + str(HOME / ".local" / "bin")


def servers() -> dict[str, dict]:
    return {k: v for k, v in json.loads(SERVERS.read_text()).items() if not k.startswith("_")}


def label(name: str) -> str:
    return f"com.jm.mcp-{name}"


def url(spec: dict) -> str:
    return f"http://127.0.0.1:{spec['port']}/mcp"


def staged_env(name: str, spec: dict) -> Path | None:
    return STAGED / f"{name}.env" if spec.get("env_file") else None


def stage_env(name: str, spec: dict) -> None:
    """Copy env_file to its launchd-readable twin (0600), replacing whatever was there."""
    dst = staged_env(name, spec)
    if dst is None:
        return
    STAGED.mkdir(parents=True, exist_ok=True)
    STAGED.chmod(0o700)
    src = Path(os.path.expanduser(spec["env_file"]))
    dst.write_bytes(src.read_bytes())
    dst.chmod(0o600)


def shell_line(name: str, spec: dict) -> str:
    """The sh -c body: source the staged env file, export the env, exec the proxy around the server."""
    parts = []
    staged = staged_env(name, spec)
    if staged is not None:
        parts.append(f". {shlex.quote(str(staged))}")
    for k, v in (spec.get("env") or {}).items():
        parts.append(f'export {k}="{v}"')      # double quotes: $VARS from env_file expand
    cmd = " ".join(shlex.quote(c) for c in spec["command"])
    parts.append(f"exec {shlex.quote(str(PROXY))} --host 127.0.0.1 --port {int(spec['port'])}"
                 f" --pass-environment -- {cmd}")
    return " && ".join(parts)


def plist(name: str, spec: dict) -> dict:
    return {
        "Label": label(name),
        "ProgramArguments": ["/bin/sh", "-c", shell_line(name, spec)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "WorkingDirectory": str(HOME),
        "StandardOutPath": str(LOGS / f"mcp-{name}.log"),
        "StandardErrorPath": str(LOGS / f"mcp-{name}.err"),
        "EnvironmentVariables": {"PATH": PATH, "HOME": str(HOME)},
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def domain() -> str:
    return f"gui/{os.getuid()}"


def install(names: list[str]) -> int:
    AGENTS.mkdir(parents=True, exist_ok=True)
    for name in names:
        spec = servers()[name]
        stage_env(name, spec)
        p = AGENTS / f"{label(name)}.plist"
        p.write_bytes(plistlib.dumps(plist(name, spec)))
        _launchctl("bootout", f"{domain()}/{label(name)}")
        for _ in range(20):                      # bootout is asynchronous: wait until the job is gone
            if _launchctl("print", f"{domain()}/{label(name)}").returncode != 0:
                break
            time.sleep(0.5)
        for attempt in range(3):                 # a bootstrap right after a bootout can still fail with EIO
            r = _launchctl("bootstrap", domain(), str(p))
            if r.returncode == 0:
                break
            time.sleep(2)
        print(f"{name}: {'started' if r.returncode == 0 else 'bootstrap failed: ' + r.stderr.strip()} -> {url(spec)}")
    return 0


def uninstall(names: list[str]) -> int:
    for name in names:
        r = _launchctl("bootout", f"{domain()}/{label(name)}")
        print(f"{name}: {'stopped' if r.returncode == 0 else r.stderr.strip() or 'not loaded'}")
    return 0


def probe(spec: dict, timeout: float = 3.0) -> str:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{spec['port']}/status", timeout=timeout) as r:
            d = json.loads(r.read().decode())
        inst = ",".join(f"{k}={v}" for k, v in (d.get("server_instances") or {}).items())
        return f"up ({inst}), last activity {str(d.get('api_last_activity', '?'))[11:19]}Z"
    except Exception as e:  # noqa: BLE001
        return f"down ({type(e).__name__})"


def launchd_pid(name: str) -> str:
    r = _launchctl("print", f"{domain()}/{label(name)}")
    for line in r.stdout.splitlines():
        if line.strip().startswith("pid = "):
            return line.split("=", 1)[1].strip()
    return "-"


def status(names: list[str]) -> int:
    ps = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,rss=,command="], capture_output=True, text=True).stdout
    for name in names:
        spec = servers()[name]
        pid = launchd_pid(name)
        kids = [l.split(None, 3) for l in ps.splitlines() if l.split()[1:2] == [pid]] if pid != "-" else []
        kid = ", ".join(f"{k[0]} {int(k[2]) // 1024} MB {k[3][:40]}" for k in kids) or "no child"
        print(f"{name:20s} launchd pid {pid:>6}  {probe(spec):45s}  child: {kid}")
    return 0


def claude_entries(names: list[str]) -> dict[str, dict]:
    return {n: {"type": "http", "url": url(servers()[n])} for n in names}


def claude_config(names: list[str], apply: bool) -> int:
    entries = claude_entries(names)
    if not apply:
        print(json.dumps(entries, indent=2))
        return 0
    cfg = HOME / ".claude.json"
    bak = cfg.with_name(f".claude.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(cfg, bak)
    data = json.loads(cfg.read_text())
    before = {n: data.get("mcpServers", {}).get(n) for n in names}
    data.setdefault("mcpServers", {}).update(entries)
    cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"backup: {bak}")
    for n in names:
        print(f"{n}: {json.dumps(before[n])[:70]} -> {entries[n]['url']}")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("install", "uninstall", "status", "claude-config"):
        print(__doc__); return 2
    names = [a for a in argv[1:] if not a.startswith("--")] or list(servers())
    unknown = [n for n in names if n not in servers()]
    if unknown:
        print(f"unknown server(s): {', '.join(unknown)}; known: {', '.join(servers())}"); return 2
    if argv[0] == "install":
        return install(names)
    if argv[0] == "uninstall":
        return uninstall(names)
    if argv[0] == "status":
        return status(names)
    return claude_config(names, "--apply" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
