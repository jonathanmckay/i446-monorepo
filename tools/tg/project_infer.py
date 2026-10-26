"""Infer a Toggl project for a new entry typed without one.

Two tiers, cheapest first (task 6hqxfpPgRG33qM9c, 2026-10-26):

1. History map: normalized description -> most frequent project over the last
   HISTORY_DAYS of the local Toggl store (~/vault/i156/toggl.sqlite). Cached to
   a small JSON file under ~/.local/state/jm/, rebuilt when older than a day.
   Exact normalized match first, then the first-two-words / first-word prefix
   with a stricter count. Accepted only on a clear majority.
2. Claude Haiku, given the valid project codes plus a few example descriptions
   per code, answers with one code or "none". Hard ~3 s timeout; any failure
   (no key, network, junk answer) means "no project". Never raises.

Sleep (睡觉) is never an inferred answer: sleep entries have their own handling.
Set TG_NO_INFER=1 to disable inference entirely (the tests' conftest does).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

DB_PATH = Path(os.environ.get("TOGGL_DB") or (Path.home() / "vault" / "i156" / "toggl.sqlite"))
_STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")) / "jm"
CACHE_PATH = _STATE_DIR / "tg-project-history.json"
CACHE_MAX_AGE = 24 * 3600
HISTORY_DAYS = 180

MIN_SHARE = 0.70        # winner's share of all occurrences of the key
MIN_EXACT = 3           # occurrences for an exact normalized match
MIN_PREFIX = 5          # occurrences for a first-word(s) prefix match
EXAMPLES_PER_CODE = 6

MODEL = "claude-haiku-5-5"
HAIKU_TIMEOUT = 3.0
API_URL = "https://api.anthropic.com/v1/messages"

NEVER_INFER = {"睡觉", "no project", ""}

_ANNOT = re.compile(r"\(\d*\)|\[\d*G?\]|\{\d*\}")


def normalize(desc: str) -> str:
    """lowercase; drop (N)/[N]/{N} annotations, @code/#tag tokens, punctuation
    and purely numeric tokens (times, counts). Digits inside a word survive so
    codes like o314 / 1s / -1g stay distinct."""
    s = _ANNOT.sub(" ", (desc or "").lower())
    s = re.sub(r"(?<!\S)[@#]\S+", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(w for w in s.split() if not w.isdigit())


def _prefixes(norm: str) -> list[tuple[str, str]]:
    words = norm.split()
    out = []
    if len(words) >= 3:
        out.append(("p2", " ".join(words[:2])))
    if len(words) >= 2 and len(words[0]) >= 3:
        out.append(("p1", words[0]))
    return out


def build_map(db_path: Path = DB_PATH, valid: set | None = None,
              days: int = HISTORY_DAYS) -> dict:
    """{"exact"|"p1"|"p2": {key: [project, winner_count, total]}, "examples":
    {project: [desc, ...]}} from the last `days` of history."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT description, project FROM entries "
            "WHERE day >= date('now', ?) AND description != ''",
            (f"-{int(days)} day",)).fetchall()
    finally:
        conn.close()
    counts = {"exact": defaultdict(Counter), "p1": defaultdict(Counter),
              "p2": defaultdict(Counter)}
    ex = defaultdict(Counter)
    for desc, proj in rows:
        if not proj or proj in NEVER_INFER or (valid and proj not in valid):
            continue
        norm = normalize(desc)
        if not norm:
            continue
        counts["exact"][norm][proj] += 1
        for kind, key in _prefixes(norm):
            counts[kind][key][proj] += 1
        ex[proj][desc.strip()[:50]] += 1
    out: dict = {"built": time.time(), "days": days}
    for kind, table in counts.items():
        out[kind] = {}
        for key, c in table.items():
            total = sum(c.values())
            if total < MIN_EXACT:
                continue
            proj, n = c.most_common(1)[0]
            out[kind][key] = [proj, n, total]
    out["examples"] = {p: [d for d, _ in c.most_common(EXAMPLES_PER_CODE)]
                       for p, c in ex.items()}
    return out


def load_map(valid: set | None = None, rebuild: bool = True) -> dict:
    """Cached history map; rebuilt from sqlite when missing or older than a
    day (falls back to the stale cache if the rebuild fails). {} on failure."""
    cached = {}
    try:
        cached = json.loads(CACHE_PATH.read_text())
    except (OSError, ValueError):
        pass
    fresh = cached and time.time() - cached.get("built", 0) < CACHE_MAX_AGE
    if fresh or not rebuild:
        return cached
    try:
        m = build_map(DB_PATH, valid)
    except Exception:  # noqa: BLE001
        return cached
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(m, ensure_ascii=False))
        tmp.replace(CACHE_PATH)
    except OSError:
        pass
    return m


def _confident(hit, min_count: int) -> str:
    if not hit:
        return ""
    proj, n, total = hit
    if n >= min_count and n / total >= MIN_SHARE and proj not in NEVER_INFER:
        return proj
    return ""


def from_history(desc: str, hmap: dict, valid: set | None = None) -> str:
    norm = normalize(desc)
    if not norm or not hmap:
        return ""
    proj = _confident(hmap.get("exact", {}).get(norm), MIN_EXACT)
    if not proj:
        for kind, key in _prefixes(norm):
            proj = _confident(hmap.get(kind, {}).get(key), MIN_PREFIX)
            if proj:
                break
    if proj and valid and proj not in valid:
        return ""
    return proj


def _api_key() -> str | None:
    """Same lookup as tools/did/shorten.py: env, then ~/vault/.env, then the
    export in ~/.zshrc (launchd/ssh callers never source .zshrc)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "did"))
        from shorten import _api_key as k  # noqa: PLC0415
        return k()
    except Exception:  # noqa: BLE001
        return os.environ.get("ANTHROPIC_API_KEY")


SERVER_PY = Path(__file__).resolve().parent.parent.parent / "mcp/toggl_server/server.py"


def _code_hints() -> dict:
    """{code: "media & consumption: news, podcasts, ..."} parsed from the toggl
    MCP server's instructions (the one existing description of each code).
    Text-parsed, not imported: server.py pulls in FastMCP."""
    try:
        return {m.group(1): m.group(2).strip() for m in re.finditer(
            r"^- (\S+) — (.+)$", SERVER_PY.read_text(), re.M)}
    except OSError:
        return {}


def _prompt(desc: str, codes: list[str], examples: dict) -> str:
    hints = _code_hints()
    lines = []
    for c in codes:
        ex = examples.get(c) or []
        hint = f" [{hints[c]}]" if c in hints else ""
        lines.append(f"{c}{hint}: " + ("; ".join(ex) if ex else "(no recent examples)"))
    return (
        "You classify a time-tracker entry into one project code. Each code has "
        "an optional [description] and the user's most frequent recent entries "
        "for it (the examples reflect actual usage; trust them over the "
        "description):\n" + "\n".join(lines) +
        f"\n\nEntry: {desc}\n\n"
        "Answer with exactly one code from the list, or none if no code clearly "
        "fits. Output only the code or none."
    )


def from_haiku(desc: str, valid: set, examples: dict,
               timeout: float = HAIKU_TIMEOUT, post=None) -> str:
    """One Haiku call -> a valid code or "". `post` is injectable for tests."""
    codes = sorted(c for c in valid if c not in NEVER_INFER)
    if not desc.strip() or not codes:
        return ""
    try:
        key = _api_key()
        if not key:
            return ""
        body = json.dumps({
            # thinking off: Haiku 5.5 otherwise spends max_tokens on an empty
            # thinking block and returns no text at all.
            "model": MODEL, "max_tokens": 10, "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": _prompt(desc, codes, examples)}],
        }).encode()
        if post is None:
            req = urllib.request.Request(API_URL, data=body, method="POST", headers={
                "x-api-key": key, "anthropic-version": "2023-06-01",
                "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read())
        else:
            data = post(body, timeout)
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")
        ans = text.strip().strip("`\"'.").strip()
        return ans if ans in codes else ""
    except Exception:  # noqa: BLE001 -- inference must never block an entry
        return ""


def infer(desc: str, valid: set) -> tuple[str, str]:
    """(project, source) with source "history" | "haiku", or ("", "")."""
    if os.environ.get("TG_NO_INFER") or not (desc or "").strip():
        return "", ""
    try:
        hmap = load_map(valid)
    except Exception:  # noqa: BLE001
        hmap = {}
    proj = from_history(desc, hmap, valid)
    if proj:
        return proj, "history"
    proj = from_haiku(desc, valid, hmap.get("examples", {}))
    if proj:
        return proj, "haiku"
    return "", ""
