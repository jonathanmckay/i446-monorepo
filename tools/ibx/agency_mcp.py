#!/usr/bin/env python3
"""
agency_mcp — Shared client for Agency MCP servers (mail, teams, etc).
Starts the server process, handles SSE JSON-RPC protocol.
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

AGENCY_BIN = os.environ.get(
    "AGENCY_BIN",
    str(Path.home() / ".config/agency/CurrentVersion/agency"),
)

# Cache running server processes + ports
_servers = {}  # name -> {"proc": Popen, "port": int}
_lock = threading.Lock()


def _start_server(name, port=0):
    """Start an agency MCP server and return its port."""
    proc = subprocess.Popen(
        [AGENCY_BIN, "mcp", name, "--transport", "http", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Read lines until we get the port number (first line that's just digits)
    deadline = time.time() + 30
    actual_port = None
    while time.time() < deadline:
        line = proc.stdout.readline().strip()
        if line.isdigit():
            actual_port = int(line)
            break
    if actual_port is None:
        proc.kill()
        raise RuntimeError(f"Failed to start agency mcp {name}")
    return proc, actual_port


def get_server(name):
    """Get or start an agency MCP server, return its port."""
    with _lock:
        if name in _servers:
            srv = _servers[name]
            if srv["proc"].poll() is None:
                return srv["port"]
            # Server died, restart
            del _servers[name]
        proc, port = _start_server(name)
        _servers[name] = {"proc": proc, "port": port}
        return port


def call_tool(server_name, tool_name, arguments=None, timeout=180):
    """Call an MCP tool and return the result. Handles SSE chunked responses."""
    import socket
    import select

    port = get_server(server_name)
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments or {},
        },
    })
    payload_bytes = payload.encode()

    # Use raw socket — Connection: close to avoid chunked encoding issues
    sock = socket.create_connection(("localhost", port), timeout=30)
    request = (
        f"POST / HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(payload_bytes)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode() + payload_bytes
    sock.sendall(request)

    # Read until connection closes, with overall timeout
    chunks = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        ready = select.select([sock], [], [], min(remaining, 5.0))
        if ready[0]:
            chunk = sock.recv(1048576)  # 1MB reads
            if not chunk:
                break
            chunks.append(chunk)
            # Check if we already have a complete "data:" line with result
            partial = b"".join(chunks).decode(errors="replace")
            for line in partial.splitlines():
                line = line.strip()
                if line.startswith("data:") and '"result"' in line:
                    try:
                        data = json.loads(line[5:].strip())
                        if "result" in data:
                            sock.close()
                            return data["result"]
                        if "error" in data:
                            sock.close()
                            raise RuntimeError(f"MCP error: {data['error']}")
                    except json.JSONDecodeError:
                        continue
    sock.close()

    # Final parse of accumulated response
    body = b"".join(chunks).decode(errors="replace")
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
                if "result" in data:
                    return data["result"]
                if "error" in data:
                    raise RuntimeError(f"MCP error: {data['error']}")
            except json.JSONDecodeError:
                continue

    raise RuntimeError(f"No result in MCP response for {tool_name}")


def stop_all():
    """Stop all running MCP servers."""
    with _lock:
        for name, srv in _servers.items():
            try:
                srv["proc"].kill()
            except Exception:
                pass
        _servers.clear()
