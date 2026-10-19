#!/usr/bin/env python3
"""Short, phone-width topic labels for Claude Code sessions (cc-pick on Imago).

A session's full title (custom /rename > Claude's ai-title > first prompt) is
too long for a Termius column on the phone and often reads as a prompt, not a
topic. Haiku turns it into a <=CAP-char label of what the conversation is
about, computed ONCE per (session, title) and cached in
~/.local/state/jm/session-shortnames.json. cc-pick only ever READS the cache —
a pick must never wait on an API call — so the hook that records titles
(scripts/session-title.sh, on session start/prompt/stop) precomputes in the
background, and `--backfill N` fills in the N newest sessions on demand.

Usage:
  session_short.py <session-id> <title>   compute+cache for one session (hook path)
  session_short.py --backfill [N]         shorten the N newest indexed sessions (default 8)
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
import state_paths as _sp  # noqa: E402

SIDECAR = _sp.STATE_DIR / "session-shortnames.json"
CAP = 28  # Termius portrait on the phone: ~48 cols minus the number/mark/host columns
MODEL = "claude-haiku-4-5-20251001"
_SHORTEN = Path.home() / "i446-monorepo" / "tools" / "did" / "shorten.py"
_CS = Path.home() / "i446-monorepo" / "tools" / "cs" / "cs.py"


def _hash(title: str) -> str:
    return hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]


def _load() -> dict:
    try:
        return json.loads(SIDECAR.read_text())
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    SIDECAR.parent.mkdir(parents=True, exist_ok=True)
    tmp = SIDECAR.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=0))
    tmp.replace(SIDECAR)


def cached_short(sid: str, title: str) -> str | None:
    """The cached label for this session+title, or None. Never calls the API."""
    rec = _load().get(sid)
    if rec and rec.get("hash") == _hash(title) and rec.get("short"):
        return rec["short"]
    return None


def _load_module(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _haiku_label(title: str) -> str | None:
    """Ask Haiku what the conversation is about, in <=CAP chars. None on any failure."""
    try:
        import anthropic
    except ImportError:
        return None
    try:
        key = _load_module("dtd_shorten", _SHORTEN)._api_key()
        client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        resp = client.messages.create(
            model=MODEL, max_tokens=30,
            messages=[{"role": "user", "content": (
                f"This is the title (or opening prompt) of a Claude Code session. "
                f"Write a label of at most {CAP} characters saying what the conversation "
                "is about: a terse topic, not a sentence. Keep proper names and codes "
                "(m5x2, i9, dtd, janus, -1n). No quotes, no trailing punctuation. "
                "Return only the label.\n\n"
                f"Title: {title}")}],
        )
        short = re.sub(r"\s+", " ", resp.content[0].text.strip().strip('"').strip())
        if not short:
            return None
        return trim(short)
    except Exception:
        return None


def trim(short: str, cap: int = CAP) -> str:
    """Cut an overshooting label at a word boundary, never mid-word (the first
    live run produced "Troubleshoot cmux availabili"). Falls back to a hard
    cut only when the label is one unbroken token."""
    if len(short) <= cap:
        return short
    cut = short[:cap]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > 0 else cut).rstrip(" -–—:,")


def ensure_short(sid: str, title: str, compute=_haiku_label) -> str | None:
    """Return the label for (sid, title), computing and caching it if missing."""
    if not sid or not title:
        return None
    hit = cached_short(sid, title)
    if hit:
        return hit
    short = compute(title)
    if not short:
        return None
    data = _load()
    data[sid] = {"hash": _hash(title), "short": short}
    _save(data)
    return short


def backfill(n: int = 8) -> int:
    """Shorten the n newest indexed sessions that lack a cached label; returns how many were computed."""
    cs = _load_module("cs", _CS)
    idx = cs.load_index()
    done = 0
    for _path, r in sorted(idx.items(), key=lambda kv: -kv[1]["mtime"])[:n]:
        title = cs.title_of(r)
        if cached_short(r["id"], title):
            continue
        if ensure_short(r["id"], title):
            done += 1
    return done


def main(argv: list[str]) -> int:
    if argv[:1] == ["--backfill"]:
        n = int(argv[1]) if len(argv) > 1 else 8
        print(f"session_short: computed {backfill(n)} label(s)")
        return 0
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    sid, title = argv[0], " ".join(argv[1:])
    out = ensure_short(sid, title)
    print(out or "")
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
