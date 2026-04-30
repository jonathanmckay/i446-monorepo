#!/usr/bin/env python3
"""
agency_mcp — Shared client for Agency MCP servers (mail, teams, etc).
Starts server processes on demand, handles SSE JSON-RPC protocol.

Uses pidfiles so that a single agency process per server type is shared
across all callers (ibx0, cron jobs, prewarm, etc). Previous behavior
spawned a new process per Python session, leading to dozens of zombies.
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

        # Start fresh
        proc, port = _start_server(name)
        _servers[name] = {"proc": proc, "port": port, "pid": proc.pid}
        return port


def call_tool(server_name, tool_name, arguments=None, timeout=120):
    """Call an MCP tool via SSE HTTP. Returns the result dict."""
    port = get_server(server_name)
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments or {}},
    }).encode()

    sock = socket.create_connection(("localhost", port), timeout=10)
    sock.sendall(
        f"POST / HTTP/1.1\r\nHost: localhost:{port}\r\n"
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
    raise RuntimeError(f"No result from {server_name}/{tool_name} (timeout={timeout}s)")


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
