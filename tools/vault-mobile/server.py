#!/usr/bin/env python3
"""vault-mobile — sync + capture backend for the native phone app.

Runs on ix (always-on). Reached through Tailscale serve (`/vault/` on the
tailnet) and the Funnel (`/vault/` on https://ix.tail9c51d5.ts.net:8443) so the
phone works with its VPN off. Tailscale strips the path prefix, so routes here
live at `/`.

Endpoints (all but /healthz require `Authorization: Bearer <device token>`):

  GET  /healthz            liveness, no auth
  GET  /manifest           full subset manifest; honours If-None-Match (304)
  GET  /file?p=<rel>       raw bytes of a manifest-listed path (never joins
                           arbitrary input onto the filesystem); X-Sha256 header
  POST /drop               new capture -> z_ibx/phone/<stamp>-<id4>-<device>.md
                           body: {"id": uuid, "created_at": ISO local, "body": str,
                                  "title": optional}; idempotent on id
  GET  /captures           last 50 captures this device dropped (for the
                           outbox-depth / "did it land" UI)

Tokens: ~/.config/vault-mobile/tokens.json  {"<device>": "<hex>"}; mint with
`python3 server.py token <device>`; revoke by deleting the entry. Compare is
constant time over every token so a leaked timing side channel yields nothing.

The phone NEVER edits existing vault files. Captures are append-only new
files, so there is nothing to merge across Syncthing / git / Obsidian Sync.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, Optional

from flask import Flask, Response, abort, g, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manifest import ManifestService, SubsetTooLarge  # noqa: E402

VAULT = Path(os.environ.get("VAULT_MOBILE_VAULT", Path.home() / "vault"))
TOKENS_FILE = Path(os.environ.get("VAULT_MOBILE_TOKENS", Path.home() / ".config" / "vault-mobile" / "tokens.json"))
CACHE_FILE = Path(os.environ.get("VAULT_MOBILE_CACHE", Path.home() / ".cache" / "vault-mobile" / "hash-cache.json"))
PORT = int(os.environ.get("VAULT_MOBILE_PORT", "5571"))
CAPTURE_DIR = "z_ibx/phone"
# Append-in-place targets (2026-10-20: "new-notes editable, just keep
# appending"). The ONLY vault files the phone may modify; everything else
# still lands as a new capture file. Idempotent on capture id via a ledger.
APPEND_TARGETS = {"z_ibx/new-notes.md"}
APPEND_LEDGER = Path(os.environ.get("VAULT_MOBILE_APPENDS",
                                    Path.home() / ".config" / "vault-mobile" / "appends.json"))
MAX_BODY = 256 * 1024  # a capture is text; anything bigger is a bug or abuse
DROP_RATE_PER_MIN = 60

log = logging.getLogger("vault-mobile")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BODY
manifests = ManifestService(VAULT, cache_path=CACHE_FILE)

_DEVICE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_ID_RE = re.compile(r"^[0-9a-fA-F-]{4,64}$")


# ---------------------------------------------------------------- tokens


def load_tokens() -> Dict[str, str]:
    try:
        d = json.loads(TOKENS_FILE.read_text())
    except FileNotFoundError:
        return {}
    return {k: v for k, v in d.items() if isinstance(v, str)}


def mint_token(device: str) -> str:
    if not _DEVICE_RE.match(device):
        raise SystemExit(f"device name must match {_DEVICE_RE.pattern}")
    toks = load_tokens()
    tok = secrets.token_hex(32)
    toks[device] = tok
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKENS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(toks, indent=1))
    os.replace(tmp, TOKENS_FILE)
    os.chmod(TOKENS_FILE, 0o600)
    return tok


def device_for(presented: str) -> Optional[str]:
    found = None
    for device, tok in load_tokens().items():  # constant time across the set
        if hmac.compare_digest(tok.encode(), presented.encode()):
            found = device
    return found


@app.before_request
def _auth():
    if request.path == "/healthz":
        return None
    hdr = request.headers.get("Authorization", "")
    if not hdr.startswith("Bearer "):
        abort(401)
    device = device_for(hdr[7:].strip())
    if device is None:
        abort(401)
    g.device = device
    return None


# ---------------------------------------------------------------- rate limit

_hits: Dict[str, Deque[float]] = defaultdict(deque)
_hits_lock = threading.Lock()


def _rate_ok(key: str, per_min: int) -> bool:
    now = time.monotonic()
    with _hits_lock:
        q = _hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= per_min:
            return False
        q.append(now)
        return True


# ---------------------------------------------------------------- routes


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "vault": str(VAULT), "ts": int(time.time())})


@app.route("/manifest")
def manifest():
    try:
        m, etag = manifests.get(force=request.args.get("force") == "1")
    except SubsetTooLarge as e:
        return jsonify({"error": "subset_too_large", "total_bytes": e.total_bytes, "cap_bytes": e.cap_bytes, "count": e.count}), 507
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers={"ETag": etag})
    resp = jsonify(m)
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/file")
def file():
    rel = request.args.get("p", "")
    try:
        entry = manifests.paths().get(rel)
    except SubsetTooLarge:
        abort(507)
    if entry is None:
        abort(404)  # not in the published set: never touch the filesystem
    path = VAULT / rel
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        abort(410)
    import hashlib

    sha = hashlib.sha256(data).hexdigest()
    resp = Response(data, mimetype="text/markdown; charset=utf-8")
    resp.headers["X-Sha256"] = sha
    resp.headers["X-Manifest-Sha256"] = entry["h"]  # differs if an agent wrote mid-sync; client re-pulls
    resp.headers["Cache-Control"] = "no-cache"
    return resp


_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}-[0-9a-f]{4}-[a-z0-9-]+\.md$")


def _capture_filename(created_at: datetime, cid: str, device: str) -> str:
    id4 = re.sub(r"[^0-9a-f]", "", cid.lower())[:4].ljust(4, "0")
    return f"{created_at.strftime('%Y-%m-%d-%H%M%S')}-{id4}-{device}.md"


def _parse_created(s: Optional[str]) -> datetime:
    if not s:
        return datetime.now()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except ValueError:
        return datetime.now()


@app.route("/drop", methods=["POST"])
def drop():
    if not _rate_ok(f"drop:{g.device}", DROP_RATE_PER_MIN):
        abort(429)
    j = request.get_json(silent=True) or {}
    cid = str(j.get("id", ""))
    body = j.get("body")
    if not _ID_RE.match(cid) or not isinstance(body, str) or not body.strip():
        return jsonify({"error": "need id (hex/uuid) and non-empty body"}), 400
    created = _parse_created(j.get("created_at"))
    title = (j.get("title") or "").strip()
    target = (j.get("target") or "").strip()
    if target:
        return _append(target, cid, body, created)
    cap_dir = VAULT / CAPTURE_DIR
    cap_dir.mkdir(parents=True, exist_ok=True)

    # Idempotent on id: a retried upload after a lost ACK must not duplicate.
    id_marker = f"id: {cid}"
    for existing in cap_dir.glob(f"*-{g.device}.md"):
        try:
            head = existing.read_text(encoding="utf-8", errors="replace")[:600]
        except OSError:
            continue
        if id_marker in head:
            return jsonify({"ok": True, "path": f"{CAPTURE_DIR}/{existing.name}", "existing": True})

    name = _capture_filename(created, cid, g.device)
    path = cap_dir / name
    if path.exists():  # same second + same id4 from the same device: practically impossible, but never clobber
        name = _capture_filename(created, secrets.token_hex(2), g.device)
        path = cap_dir / name
    fm_title = title or body.strip().splitlines()[0][:80]
    text = (
        "---\n"
        f"title: {json.dumps(fm_title, ensure_ascii=False)}\n"
        f"date: {created.strftime('%Y-%m-%d')}\n"
        "type: capture\n"
        "tags: [z_ibx, phone]\n"
        f"source: phone/{g.device}\n"
        f"created_at: {created.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        f"{id_marker}\n"
        "---\n\n"
        f"{body.strip()}\n"
    )
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    log.info("drop %s -> %s (%d bytes)", g.device, name, len(text))
    return jsonify({"ok": True, "path": f"{CAPTURE_DIR}/{name}", "existing": False}), 201


def _append_ledger() -> set:
    try:
        return set(json.loads(APPEND_LEDGER.read_text()))
    except (OSError, ValueError):
        return set()


def _append(target: str, cid: str, body: str, created: datetime):
    """Append one block to an allow-listed vault file. A date line
    (YYYY.MM.DD, the /notes skill's block-date header) is written first when
    the file has none for that day. Atomic replace; idempotent on id."""
    if target not in APPEND_TARGETS:
        return jsonify({"error": f"append not allowed: {target}"}), 400
    seen = _append_ledger()
    if cid in seen:
        return jsonify({"ok": True, "path": target, "existing": True, "appended": True})
    path = VAULT / target
    try:
        cur = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as e:
        return jsonify({"error": f"read failed: {e}"}), 500
    day = created.strftime("%Y.%m.%d")
    has_day = any(ln.strip() == day for ln in cur.splitlines())
    block = ("" if cur.endswith("\n\n") or not cur else ("\n" if cur.endswith("\n") else "\n\n"))
    if not has_day:
        block += day + "\n"
    block += body.strip() + "\n"
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(cur + block, encoding="utf-8")
    os.replace(tmp, path)
    seen.add(cid)
    try:
        APPEND_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        APPEND_LEDGER.write_text(json.dumps(sorted(seen)))
    except OSError:
        pass
    manifests.invalidate() if hasattr(manifests, "invalidate") else None
    log.info("append %s -> %s (%d bytes)", g.device, target, len(block))
    return jsonify({"ok": True, "path": target, "existing": False, "appended": True}), 201


@app.route("/captures")
def captures():
    cap_dir = VAULT / CAPTURE_DIR
    if not cap_dir.exists():
        return jsonify({"captures": []})
    names = sorted((p.name for p in cap_dir.glob(f"*-{g.device}.md")), reverse=True)[:50]
    return jsonify({"captures": names})


@app.errorhandler(401)
def _401(_e):
    return jsonify({"error": "unauthorized"}), 401


@app.errorhandler(429)
def _429(_e):
    return jsonify({"error": "rate_limited"}), 429


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "token":
        print(mint_token(sys.argv[2]))
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "tokens":
        for d in load_tokens():
            print(d)
        sys.exit(0)
    log.info("vault-mobile serving %s on 127.0.0.1:%d", VAULT, PORT)
    app.run(host="127.0.0.1", port=PORT, threaded=True)
