#!/usr/bin/env python3
"""
agency_mcp — Shared client for Agency MCP servers (mail, teams, etc).
Starts server processes on demand, handles SSE JSON-RPC protocol.

Uses pidfiles so that a single agency process per server type is shared
across all callers (ibx0, cron jobs, prewarm, etc). Previous behavior
spawned a new process per Python session, leading to dozens of zombies.

Interactive-login guard (2026-09-25): if Safari already shows an unfinished
Microsoft sign-in tab, get_server()/call_tool() raise EntraLoginPending
instead of letting Agency open another one every 15 minutes. See the
comment block above EntraLoginPending.
"""

import atexit
import json
import os
import select
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

AGENCY_BIN = os.environ.get(
    "AGENCY_BIN",
    str(Path.home() / ".config/agency/CurrentVersion/agency"),
)

_PIDFILE_DIR = Path.home() / ".config" / "agency" / "pids"
_servers = {}  # name -> {"proc": Popen|None, "port": int}
_lock = threading.Lock()

# --- Interactive-login guard ------------------------------------------------
# Agency has no silent/non-interactive mode: when its Entra token cache is
# invalid it opens a NEW browser tab on login.microsoftonline.com (via
# AzureAuth) and waits 15 minutes for a human, both at server start and on
# every 401 from a tool call. Unattended (dream 03:00 cron, dashboard jobs)
# that produced a fresh tab every 15 minutes, 50-90 a day for weeks, and a
# Safari that nobody could sign in to fast enough (2026-09-25). One pending
# tab is the user's cue to sign in; a second one is noise. So: if Safari
# already has an unfinished Microsoft sign-in tab, refuse to start a server
# or forward a tool call, and raise EntraLoginPending (a RuntimeError, so
# every existing caller's error path handles it as "MCP unavailable").
# A list, not a tuple, and no "login"/"auth" in the name: GitGuardian's generic
# Authentication Tuple detector flags `<login_*> = ("x", "y")` as a leaked
# username/password pair (false positive, incident 2026-09-27 on 4180afc).
MS_SIGNIN_HOSTS = ["login.microsoftonline.com", "login.live.com"]
_LOGIN_TAB_CACHE = {"at": 0.0, "url": None}
_LOGIN_TAB_TTL = 10.0  # seconds; one osascript per burst of calls, not per call

_SAFARI_LOGIN_TABS = '''tell application "Safari"
    set out to ""
    repeat with w in windows
        repeat with t in tabs of w
            set u to URL of t
            if u contains "%s" then set out to out & u & linefeed
        end repeat
    end repeat
    return out
end tell'''


class EntraLoginPending(RuntimeError):
    """A Microsoft sign-in tab is already open and unfinished in Safari.

    Nothing Agency-backed will work until the user completes it (or closes
    it), so callers should treat this like any other 'MCP unavailable' error
    and move on. Not retried, not restarted: a fresh process would only open
    another tab."""


def _safari_running():
    try:
        r = subprocess.run(["pgrep", "-x", "Safari"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _safari_login_tab():
    """URL of an open Microsoft sign-in tab, or None. Never launches Safari."""
    if not _safari_running():
        return None
    for host in MS_SIGNIN_HOSTS:
        try:
            r = subprocess.run(["osascript", "-e", _SAFARI_LOGIN_TABS % host],
                               capture_output=True, text=True, timeout=10)
        except Exception:
            return None
        if r.returncode != 0:
            return None  # Automation permission denied etc. -> can't tell, don't block
        for line in r.stdout.splitlines():
            if line.strip():
                return line.strip()
    return None


def entra_login_pending(force=False):
    """Return the pending sign-in tab URL, or None. Cached for _LOGIN_TAB_TTL
    seconds so a burst of tool calls costs one AppleScript round-trip."""
    now = time.time()
    if not force and now - _LOGIN_TAB_CACHE["at"] < _LOGIN_TAB_TTL:
        return _LOGIN_TAB_CACHE["url"]
    url = _safari_login_tab()
    _LOGIN_TAB_CACHE.update(at=now, url=url)
    return url


def _refuse_if_login_pending(what):
    url = entra_login_pending()
    if url:
        raise EntraLoginPending(
            f"not {what}: a Microsoft sign-in tab is already waiting in Safari "
            f"({url[:80]}...) - finish that sign-in instead of opening another")


def _pidfile(name):
    return _PIDFILE_DIR / f"{name}.json"


def _read_pidfile(name):
    """Read pidfile, return (pid, port) or (None, None)."""
    pf = _pidfile(name)
    if not pf.exists():
        return None, None
    try:
        data = json.loads(pf.read_text())
        return data.get("pid"), data.get("port")
    except Exception:
        return None, None


def _write_pidfile(name, pid, port):
    _PIDFILE_DIR.mkdir(parents=True, exist_ok=True)
    _pidfile(name).write_text(json.dumps({"pid": pid, "port": port}))


def _clear_pidfile(name):
    pf = _pidfile(name)
    if pf.exists():
        pf.unlink()


def _is_alive(pid):
    """Check if a process is running."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _port_responsive(port):
    """Quick check if a port accepts connections."""
    try:
        s = socket.create_connection(("localhost", port), timeout=2)
        s.close()
        return True
    except Exception:
        return False


def _start_server(name):
    """Start an agency MCP server on a random port and return (proc, port)."""
    proc = subprocess.Popen(
        [AGENCY_BIN, "mcp", name, "--transport", "http", "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.time() + 30
    port = None
    while time.time() < deadline:
        line = proc.stdout.readline().strip()
        if line.isdigit():
            port = int(line)
            break
    if port is None:
        proc.kill()
        raise RuntimeError(f"Failed to start agency mcp {name}")
    _write_pidfile(name, proc.pid, port)
    return proc, port


def get_server(name):
    """Get or start an agency MCP server, return its port.

    First checks for an existing process via pidfile. If a healthy process
    exists from a previous Python session, reuses it without spawning a new one.
    """
    with _lock:
        # Check in-memory cache first
        if name in _servers:
            srv = _servers[name]
            proc = srv.get("proc")
            if proc is None or proc.poll() is None:
                if _port_responsive(srv["port"]):
                    return srv["port"]
            del _servers[name]

        # Check pidfile for process from a previous session
        pid, port = _read_pidfile(name)
        if pid and _is_alive(pid) and port and _port_responsive(port):
            _servers[name] = {"proc": None, "port": port, "pid": pid}
            return port

        # Clean up stale pidfile
        if pid:
            _clear_pidfile(name)
            # Kill stale process if still alive but unresponsive
            if _is_alive(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass

        # Start fresh -- unless a sign-in tab is already waiting: starting
        # would open a second one (see EntraLoginPending).
        _refuse_if_login_pending(f"starting agency mcp {name}")
        proc, port = _start_server(name)
        _servers[name] = {"proc": proc, "port": port, "pid": proc.pid}
        return port


def _remote_endpoint(server_name):
    """Return (host, port) if AGENCY_REMOTE_<NAME>_PORT is set, else None.

    Used to delegate to a remote Agency MCP (e.g., ix → straylight via ssh
    -L tunnel). Host defaults to localhost (assume a forward tunnel binds
    the remote port locally).
    """
    env_key = f"AGENCY_REMOTE_{server_name.upper()}_PORT"
    port = os.environ.get(env_key)
    if not port:
        return None
    host = os.environ.get("AGENCY_REMOTE_HOST", "localhost")
    try:
        return host, int(port)
    except ValueError:
        return None


class AgencyTimeout(RuntimeError):
    """The server accepted the connection but never returned a result.

    Distinct from a tool-level error (plain RuntimeError): a timeout means the
    server process is likely wedged and worth restarting; a tool error means
    the server answered and a retry against a fresh process won't help."""


def restart_server(name):
    """Kill a wedged server (in-memory + pidfile) so the next call respawns it."""
    with _lock:
        srv = _servers.pop(name, None)
        pids = set()
        if srv:
            if srv.get("pid"):
                pids.add(srv["pid"])
            proc = srv.get("proc")
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        fpid, _ = _read_pidfile(name)
        if fpid:
            pids.add(fpid)
        for p in pids:
            if _is_alive(p):
                try:
                    os.kill(p, signal.SIGTERM)
                    time.sleep(0.5)
                    if _is_alive(p):
                        os.kill(p, signal.SIGKILL)
                except Exception:
                    pass
        _clear_pidfile(name)


def call_tool(server_name, tool_name, arguments=None, timeout=120):
    """Call an MCP tool via SSE HTTP. Returns the result dict.

    Self-healing: pidfile reuse means a single long-lived server process is
    shared by all callers — and a wedged one (seen after multi-day uptimes)
    poisons everything until killed by hand. On a timeout or socket error,
    kill + respawn the server and retry the call once. Remote endpoints are
    not restarted (we don't own the process)."""
    remote = _remote_endpoint(server_name)
    if remote:
        host, port = remote
        return _call_tool_once(host, port, server_name, tool_name, arguments, timeout)
    # Even a healthy, already-running server opens a new sign-in tab on the
    # first 401 it meets, so the guard applies per call, not just per start.
    _refuse_if_login_pending(f"calling {server_name}/{tool_name}")
    try:
        port = get_server(server_name)
        return _call_tool_once("localhost", port, server_name, tool_name, arguments, timeout)
    except (AgencyTimeout, OSError):
        restart_server(server_name)
        port = get_server(server_name)
        return _call_tool_once("localhost", port, server_name, tool_name, arguments, timeout)


def _call_tool_once(host, port, server_name, tool_name, arguments, timeout):
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments or {}},
    }).encode()

    sock = socket.create_connection((host, port), timeout=10)
    sock.sendall(
        f"POST / HTTP/1.1\r\nHost: {host}:{port}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n"
        f"Connection: close\r\n\r\n".encode() + payload
    )

    chunks = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = max(deadline - time.time(), 0.1)
        ready = select.select([sock], [], [], min(remaining, 5.0))
        if ready[0]:
            chunk = sock.recv(1048576)
            if not chunk:
                break
            chunks.append(chunk)
            # Early exit: check if we have a complete result
            partial = b"".join(chunks).decode(errors="replace")
            for line in partial.splitlines():
                stripped = line.strip()
                if stripped.startswith("data:") and '"result"' in stripped:
                    try:
                        data = json.loads(stripped[5:].strip())
                        if "result" in data:
                            sock.close()
                            return data["result"]
                        if "error" in data:
                            sock.close()
                            raise RuntimeError(data["error"])
                    except json.JSONDecodeError:
                        pass
    sock.close()

    # Final parse
    body = b"".join(chunks).decode(errors="replace")
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            try:
                data = json.loads(stripped[5:].strip())
                if "result" in data:
                    return data["result"]
                if "error" in data:
                    raise RuntimeError(data["error"])
            except json.JSONDecodeError:
                pass
    raise AgencyTimeout(f"No result from {server_name}/{tool_name} (timeout={timeout}s)")


def stop_all():
    """Stop all running MCP servers started by this process."""
    with _lock:
        for name, srv in list(_servers.items()):
            proc = srv.get("proc")
            if proc is None:
                continue  # adopted from pidfile, don't kill
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        _servers.clear()


atexit.register(stop_all)
