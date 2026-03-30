"""
m5x2 AI Proxy — transparent Anthropic API proxy with per-user logging.

Each user sets:
  export ANTHROPIC_API_KEY="pk-<user>-<token>"   (proxy key, not real Anthropic key)
  export ANTHROPIC_BASE_URL="https://m5x2-ai-proxy.fly.dev"

The proxy validates the key, swaps in the real Anthropic key, forwards the
request, and logs each turn (tokens + full content) to SQLite.
"""

import json
import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
ANTHROPIC_BASE = "https://api.anthropic.com"
DB_PATH = Path(os.environ.get("DB_PATH", "proxy.db"))
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("proxy")

# ── Users ─────────────────────────────────────────────────────────────────────

def load_users() -> dict:
    """Load from USERS_JSON env var (prod) or local users.json (dev)."""
    raw = os.environ.get("USERS_JSON")
    if raw:
        return json.loads(raw)
    p = Path(__file__).parent / "users.json"
    return json.loads(p.read_text()) if p.exists() else {}


# ── Database ──────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS turns (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                ts                  TEXT    NOT NULL,
                user_id             TEXT    NOT NULL,
                session_id          TEXT,
                model               TEXT,
                input_tokens        INTEGER DEFAULT 0,
                output_tokens       INTEGER DEFAULT 0,
                cache_read_tokens   INTEGER DEFAULT 0,
                cache_write_tokens  INTEGER DEFAULT 0,
                input_messages      TEXT,
                output_content      TEXT,
                stop_reason         TEXT,
                duration_ms         INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_turns_user ON turns(user_id);
            CREATE INDEX IF NOT EXISTS idx_turns_ts   ON turns(ts);
        """)


def write_turn(
    user_id: str,
    session_id: str | None,
    model: str,
    usage: dict,
    input_messages: list,
    output_content: str,
    stop_reason: str | None,
    duration_ms: int,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO turns
               (ts, user_id, session_id, model,
                input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                input_messages, output_content, stop_reason, duration_ms)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                user_id,
                session_id,
                model,
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                usage.get("cache_read_input_tokens", 0),
                usage.get("cache_creation_input_tokens", 0),
                json.dumps(input_messages),
                output_content,
                stop_reason,
                duration_ms,
            ),
        )


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log.info(f"DB ready at {DB_PATH}")
    yield


app = FastAPI(lifespan=lifespan)


# ── Auth ──────────────────────────────────────────────────────────────────────

def authenticate(request: Request) -> dict:
    key = request.headers.get("x-api-key", "")
    if key.startswith("Bearer "):
        key = key[7:]
    user = load_users().get(key)
    if not user:
        raise HTTPException(401, "Invalid proxy key")
    return user


# ── Header helpers ────────────────────────────────────────────────────────────

_STRIP = {"host", "content-length", "x-api-key", "authorization"}


def upstream_headers(request: Request) -> dict:
    h = {k: v for k, v in request.headers.items() if k.lower() not in _STRIP}
    h["x-api-key"] = ANTHROPIC_KEY
    return h


# ── SSE parser ────────────────────────────────────────────────────────────────

def parse_sse_line(line: str, state: dict) -> None:
    """Extract token counts and text from one SSE data line into state."""
    if not line.startswith("data: "):
        return
    data_str = line[6:].strip()
    if data_str in ("", "[DONE]"):
        return
    try:
        ev = json.loads(data_str)
    except json.JSONDecodeError:
        return

    t = ev.get("type", "")
    if t == "message_start":
        state["usage"].update(ev.get("message", {}).get("usage", {}))
    elif t == "content_block_delta":
        delta = ev.get("delta", {})
        if delta.get("type") == "text_delta":
            state["text"].append(delta.get("text", ""))
    elif t == "message_delta":
        state["usage"].update(ev.get("usage", {}))
        state["stop_reason"] = ev.get("delta", {}).get("stop_reason")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/v1/messages")
async def messages(request: Request):
    user = authenticate(request)
    body_bytes = await request.body()
    body = json.loads(body_bytes)
    headers = upstream_headers(request)
    session_id = request.headers.get("x-session-id")
    model = body.get("model", "unknown")
    url = f"{ANTHROPIC_BASE}/v1/messages"
    t0 = time.monotonic()

    # ── Non-streaming ─────────────────────────────────────────────────────────
    if not body.get("stream"):
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(url, headers=headers, json=body)

        duration_ms = int((time.monotonic() - t0) * 1000)

        if resp.status_code == 200:
            rb = resp.json()
            output = "".join(
                b.get("text", "") for b in rb.get("content", []) if b.get("type") == "text"
            )
            try:
                write_turn(
                    user["user_id"], session_id, model, rb.get("usage", {}),
                    body.get("messages", []), output, rb.get("stop_reason"), duration_ms,
                )
            except Exception as e:
                log.error(f"DB write failed: {e}")

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers={"content-type": "application/json"},
        )

    # ── Streaming ─────────────────────────────────────────────────────────────
    state: dict = {"text": [], "usage": {}, "stop_reason": None}
    buf = ""

    async def generate() -> AsyncGenerator[bytes, None]:
        nonlocal buf
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk
                    buf += chunk.decode("utf-8", errors="replace")
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        parse_sse_line(line.strip(), state)

        duration_ms = int((time.monotonic() - t0) * 1000)
        try:
            write_turn(
                user["user_id"], session_id, model, state["usage"],
                body.get("messages", []), "".join(state["text"]),
                state["stop_reason"], duration_ms,
            )
        except Exception as e:
            log.error(f"DB write failed: {e}")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def passthrough(path: str, request: Request):
    """Forward all other Anthropic endpoints (e.g. GET /v1/models) unchanged."""
    authenticate(request)
    body_bytes = await request.body()
    headers = upstream_headers(request)
    url = f"{ANTHROPIC_BASE}/{path}"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.request(request.method, url, headers=headers, content=body_bytes)
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={"content-type": resp.headers.get("content-type", "application/json")},
    )


# ── Admin API ─────────────────────────────────────────────────────────────────

def require_admin(request: Request) -> None:
    if not ADMIN_KEY or request.headers.get("x-admin-key") != ADMIN_KEY:
        raise HTTPException(403, "Forbidden")


@app.get("/admin/turns")
async def admin_turns(request: Request, limit: int = 200, user_id: str = ""):
    require_admin(request)
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        if user_id:
            rows = conn.execute(
                "SELECT * FROM turns WHERE user_id=? ORDER BY ts DESC LIMIT ?",
                [user_id, limit],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM turns ORDER BY ts DESC LIMIT ?", [limit]
            ).fetchall()
    return [dict(r) for r in rows]


@app.get("/admin/stats")
async def admin_stats(request: Request):
    require_admin(request)
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                user_id,
                COUNT(*)            AS turns,
                SUM(input_tokens)   AS input_tokens,
                SUM(output_tokens)  AS output_tokens,
                SUM(cache_read_tokens)  AS cache_read_tokens,
                SUM(cache_write_tokens) AS cache_write_tokens
            FROM turns
            GROUP BY user_id
            ORDER BY turns DESC
        """).fetchall()
    cols = ["user_id", "turns", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"]
    return [dict(zip(cols, r)) for r in rows]


@app.get("/health")
async def health():
    return {"status": "ok"}
