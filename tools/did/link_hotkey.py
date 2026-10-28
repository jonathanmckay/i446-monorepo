#!/usr/bin/env python3
"""link_hotkey: the links in the focused cmux pane, each routed to the right
Chrome profile or to Obsidian (2026-10-28).

~/.hammerspoon/link-hotkey.lua runs this on ⌃⌥L and opens what it prints:
  {"links": [{"kind": "web", "url", "profile", "pname"} |
             {"kind": "obsidian", "path"}, ...]}   newest first
  {"action": "dtd"}      dtd pane: ctrl-l was sent, dtd opens its own link
  {"error": "..."}

Where the links come from:
  - Claude Code pane: the session transcript's last assistant messages (full,
    unwrapped URLs; the screen hard-wraps them and hides markdown targets);
  - anything else (janus, a shell): the visible screen text;
  - dtd: its rows only show "(link)", so it gets ctrl-l instead.

Which Chrome profile a web link gets, first rule that applies:
  1. the URL itself (Microsoft hosts → MSFT, AppFolio/m5x2 → m5c7.com);
  2. the label of the dtd task this pane's Claude session has claimed;
  3. the session's working directory (m5x2 repos → m5c7.com);
  4. 个.

Python 3.9 (Hammerspoon runs /usr/bin/python3) and absolute paths throughout:
hs.task gets a bare PATH.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dtd_link import DEFAULT_PROFILE, PROFILES, urls_in  # noqa: E402

CMUX = "/Applications/cmux.app/Contents/Resources/bin/cmux"
CMUX_PASSWORD_FILE = Path.home() / ".local/state/cmux/socket-control-password"
HOME = Path.home()
VAULT = (HOME / "vault").resolve()
SESSIONS_DIR = HOME / ".claude/sessions"
PROJECTS_DIR = HOME / ".claude/projects"
CLAIMS_DIR = HOME / "vault/z_ibx/agent-claims/by-session"
TASK_QUEUE = HOME / ".local/state/jm/task-queue.json"
TRANSCRIPT_MESSAGES = 40       # assistant text blocks scanned, newest first
                               # (each tool step of a turn is its own block)
MAX_LINKS = 9

MSFT, M5C7 = PROFILES["i9"], PROFILES["m5x2"]
MSFT_HOSTS = ("microsoft.com", "sharepoint.com", "office.com", "office365.com",
              "microsoftonline.com", "cloud.microsoft", "visualstudio.com", "azure.com",
              "powerbi.com", "aka.ms", "xbox.com", "microsoftstream.com")
MSFT_GITHUB_ORGS = ("microsoft", "gaming-microsoft", "xbox", "azure")
M5C7_HOSTS = ("appfolio.com", "m5x2.com", "m5c7.com")

OBSIDIAN = re.compile(r"obsidian://open\?[^\s<>()\[\]\"'`]+")
PATH = re.compile(r"(?:~|/Users/mckay)?/?[\w.\-~/]*[\w\-]\.md(?::\d+)?\b")


def cmux(*args: str) -> str:
    env = dict(os.environ)
    if "CMUX_SOCKET_PASSWORD" not in env and CMUX_PASSWORD_FILE.exists():
        env["CMUX_SOCKET_PASSWORD"] = CMUX_PASSWORD_FILE.read_text().strip()
    r = subprocess.run([CMUX, *args], capture_output=True, text=True, timeout=3, env=env)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:160] or f"cmux {args[0]} failed")
    return r.stdout


def focused_surface(tree: dict) -> dict | None:
    """The key window's focused pane's selected surface. Not the top-level
    "active", which follows the caller's own pane."""
    for w in tree.get("windows", []):
        if not w.get("key"):
            continue
        for ws in w.get("workspaces", []):
            if not ws.get("active"):
                continue
            for p in ws.get("panes", []):
                if p.get("focused"):
                    for s in p.get("surfaces", []):
                        if s.get("ref") == p.get("selected_surface_ref"):
                            return s
    return None


def foreground(tty: str) -> list[tuple[int, str]]:
    """(pid, command) of the tty's foreground process group."""
    r = subprocess.run(["/bin/ps", "-t", tty, "-o", "pid=,pgid=,tpgid=,command="],
                       capture_output=True, text=True, timeout=3)
    out = []
    for line in r.stdout.splitlines():
        f = line.split(None, 3)
        if len(f) == 4 and f[1] == f[2]:
            out.append((int(f[0]), f[3]))
    return out


def is_dtd(surface: dict, procs: list[tuple[int, str]]) -> bool:
    if any("tools/did/dtd.sh" in c or ("fzf" in c and "dtd" in c) for _, c in procs):
        return True
    # dtd over ssh on Ix runs nothing locally; its tab is titled "N · dtd"
    return (surface.get("title") or "").rstrip().endswith("· dtd")


def claude_session(procs: list[tuple[int, str]]) -> dict | None:
    for pid, cmd in procs:
        if not re.match(r"(?:\S*/)?claude(?:\s|$)", cmd):
            continue
        try:
            s = json.loads((SESSIONS_DIR / f"{pid}.json").read_text())
        except (OSError, ValueError):
            continue
        if s.get("kind", "interactive") == "interactive" and s.get("sessionId"):
            return s
    return None


def transcript_text(session_id: str) -> str:
    """The recent assistant text blocks, newest first."""
    paths = glob.glob(str(PROJECTS_DIR / "*" / f"{session_id}.jsonl"))
    if not paths:
        return ""
    with open(max(paths, key=os.path.getmtime), "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 2_000_000))
        lines = f.read().decode("utf-8", "replace").splitlines()
    texts: list[str] = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "assistant":
            continue
        content = (e.get("message") or {}).get("content") or []
        t = "\n".join(c.get("text", "") for c in content
                      if isinstance(c, dict) and c.get("type") == "text")
        if t.strip():
            texts.append(t)
            if len(texts) >= TRANSCRIPT_MESSAGES:
                break
    return "\n".join(texts)


def unwrap_screen(text: str) -> str:
    """Rejoin lines the terminal wrapped at full width, newest line first."""
    lines = text.rstrip("\n").split("\n")
    width = max((len(x) for x in lines), default=0)
    joined: list[str] = []
    buf = ""
    for x in lines:
        buf += x.lstrip() if buf else x
        if len(x) < width:
            joined.append(buf)
            buf = ""
    if buf:
        joined.append(buf)
    return "\n".join(reversed(joined))


def claim_profile(session_id: str) -> tuple[str, str] | None:
    try:
        ids = (CLAIMS_DIR / session_id).read_text().split()
        q = json.loads(TASK_QUEUE.read_text())
    except (OSError, ValueError):
        return None
    labels = {}
    for bucket in q.values():
        if isinstance(bucket, list):
            for t in bucket:
                if isinstance(t, dict):
                    labels[str(t.get("id"))] = t.get("labels") or []
    for tid in reversed(ids):          # latest claim first
        for dom, prof in PROFILES.items():
            if dom in labels.get(tid, []):
                return prof
    return None


def cwd_profile(cwd: str) -> tuple[str, str] | None:
    c = (cwd or "").lower()
    if "m5x2" in c or "m5c7" in c:
        return M5C7
    if "gaming-microsoft" in c or "/msft" in c:
        return MSFT
    return None


def url_profile(url: str) -> tuple[str, str] | None:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    ends = lambda hs: any(host == h or host.endswith("." + h) for h in hs)  # noqa: E731
    if ends(MSFT_HOSTS):
        return MSFT
    if host == "github.com" and u.path.strip("/").split("/")[0].lower() in MSFT_GITHUB_ORGS:
        return MSFT
    if ends(M5C7_HOSTS):
        return M5C7
    return None


def vault_path(raw: str, cwd: str) -> str | None:
    """raw resolved to an existing .md inside the vault, else None."""
    p = re.sub(r":\d+$", "", raw)
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        if not cwd:
            return None
        p = os.path.join(cwd, p)
    try:
        real = Path(p).resolve()
    except OSError:
        return None
    if real.suffix != ".md" or not real.is_file():
        return None
    try:
        real.relative_to(VAULT)
    except ValueError:
        return None
    return str(real)


def extract(text: str, cwd: str, context: tuple[str, str] | None) -> list[dict]:
    """Links in text order (text is newest-first), deduplicated."""
    found: list[tuple[int, dict]] = []
    for u in urls_in(text):
        if u.endswith("…"):
            continue                    # truncated by a TUI
        prof = url_profile(u) or context or DEFAULT_PROFILE
        found.append((text.find(u), {"kind": "web", "url": u,
                                     "profile": prof[0], "pname": prof[1]}))
    for m in OBSIDIAN.finditer(text):
        path = (parse_qs(urlparse(m.group(0)).query).get("path") or [""])[0]
        vp = vault_path(unquote(path), cwd) if path else None
        if vp:
            found.append((m.start(), {"kind": "obsidian", "path": vp}))
    for m in PATH.finditer(text):
        if text[max(0, m.start() - 3):m.start()].endswith("://"):
            continue
        vp = vault_path(m.group(0), cwd)
        if vp:
            found.append((m.start(), {"kind": "obsidian", "path": vp}))
    out: list[dict] = []
    seen = set()
    for _, link in sorted(found, key=lambda x: x[0]):
        key = link.get("url") or link.get("path")
        if key not in seen:
            seen.add(key)
            out.append(link)
    return out[:MAX_LINKS]


def resolve() -> dict:
    surface = focused_surface(json.loads(cmux("--json", "tree")))
    if not surface or surface.get("type") != "terminal" or not surface.get("tty"):
        return {"error": "no focused terminal pane"}
    procs = foreground(surface["tty"])
    if is_dtd(surface, procs):
        cmux("send-key", "--surface", surface["ref"], "ctrl+l")
        return {"action": "dtd"}
    session = claude_session(procs)
    cwd = (session or {}).get("cwd", "")
    text = transcript_text(session["sessionId"]) if session else ""
    context = (claim_profile(session["sessionId"]) if session else None) or cwd_profile(cwd)
    links = extract(text, cwd, context) if text else []
    if not links:
        links = extract(unwrap_screen(cmux("read-screen", "--surface", surface["ref"])), cwd, context)
    return {"links": links} if links else {"error": "no links in this pane"}


def main() -> int:
    try:
        out = resolve()
    except Exception as e:  # noqa: BLE001
        out = {"error": str(e)[:200]}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
