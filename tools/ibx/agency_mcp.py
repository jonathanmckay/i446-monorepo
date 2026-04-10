#!/usr/bin/env python3
"""
agency_mcp — Shared client for Agency MCP servers (mail, teams, etc).
Starts server processes on demand, handles SSE JSON-RPC protocol.
"""

import atexit
import json
import os
import select
import socket
import subprocess
import threading
import time
from pathlib import Path

AGENCY_BIN = os.environ.get(
    "AGENCY_BIN",
    str(Path.home() / ".config/agency/CurrentVersion/agency"),
)

_servers = {}  # name -> {"proc": Popen, "port": int}
_lock = threading.Lock()


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
    return proc, port


def get_server(name):
    """Get or start an agency MCP server, return its port."""
    with _lock:
        if name in _servers:
            srv = _servers[name]
            if srv["proc"].poll() is None:
                return srv["port"]
            del _servers[name]
        proc, port = _start_server(name)
        _servers[name] = {"proc": proc, "port": port}
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
    """Stop all running MCP servers."""
    with _lock:
        for name, srv in list(_servers.items()):
            try:
                srv["proc"].terminate()
                srv["proc"].wait(timeout=5)
            except Exception:
                try:
                    srv["proc"].kill()
                except Exception:
                    pass
        _servers.clear()


atexit.register(stop_all)
