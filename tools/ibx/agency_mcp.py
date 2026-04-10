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


def call_tool(server_name, tool_name, arguments=None):
    """Call an MCP tool and return the result. Handles SSE chunked responses."""
    import http.client
    import urllib.parse

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

    conn = http.client.HTTPConnection("localhost", port, timeout=120)
    conn.request("POST", "/", payload, {"Content-Type": "application/json"})
    resp = conn.getresponse()

    # Read SSE stream line by line — look for "data:" lines with result
    result = None
    while True:
        line = resp.readline()
        if not line:
            break
        line = line.decode().strip()
        if line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
                if "result" in data:
                    result = data["result"]
                    break
                if "error" in data:
                    conn.close()
                    raise RuntimeError(f"MCP error: {data['error']}")
            except json.JSONDecodeError:
                continue
    conn.close()

    if result is None:
        raise RuntimeError(f"No result in MCP response for {tool_name}")
    return result


def stop_all():
    """Stop all running MCP servers."""
    with _lock:
        for name, srv in _servers.items():
            try:
                srv["proc"].kill()
            except Exception:
                pass
        _servers.clear()
