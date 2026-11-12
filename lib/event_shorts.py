"""Haiku short names for calendar event titles — ONE implementation for every
janus surface (2026-11-12, JM: "make it so that haiku will shorten the event
titles ... this should happen serverside so it's common across CLI and iOS").

Before this the TUI (tools/tg/janus.py) shortened titles on its own with the
dtd TASK-title prompt and the phone/web (tools/janus/mobile.py) showed the raw
title. Now both call this module: same model, same prompt, same sidecar format.

- `apply_cached(events, ...)` is synchronous and never touches the network: it
  sets ev["short"] from the sidecar and returns the misses.
- `fill(misses, ...)` is the Haiku round-trip (≈1 s per title); callers run it
  on a thread so a timeline fetch never waits on it. The result lands in the
  sidecar for the next pass; recurring meetings hit forever.
- `raw_for(short)` is the reverse lookup: a client that converts or logs an
  event sends the title it SAW, which may now be the short; the server maps it
  back so Toggl descriptions, the "already tracked" match and the speedy-
  meeting credit keep working on raw titles.

Sidecar: {key: {"raw": <raw title>, "short": <short>}} at
state_paths.EVENT_SHORTNAMES (per host — Straylight's TUI and Ix's server each
keep their own; same prompt, so the shorts agree in practice). The key folds a
prompt version and the column budget in, so a prompt change or a wider pane
regenerates instead of serving a stale short (the old TUI cache at
~/.cache/janus/event-shortnames.json held task-prompt shorts keyed by bare title
and is simply abandoned).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_paths  # noqa: E402

MODEL = "claude-haiku-5-5"
PROMPT_VERSION = 1
DEFAULT_COLS = 33          # the TUI's narrow-pane budget; the phone row is about the same
MAX_NEW_PER_FILL = 6       # Haiku calls per fill pass; the rest come next pass
PATH = state_paths.EVENT_SHORTNAMES
MY_NAME = "Jonathan McKay"

_lock = threading.Lock()
failed: set[str] = set()   # raw titles Haiku returned nothing for (this process)

try:
    from wcwidth import wcswidth as _wcswidth
except ImportError:  # pragma: no cover
    def _wcswidth(s: str) -> int:
        return len(s)


def dwidth(s: str) -> int:
    """Display width (CJK double-width aware) — mirrors tools/tg/janus.py."""
    w = _wcswidth(s)
    return w if w >= 0 else len(s)


def norm(title: str) -> str:
    """mobile.py's _safe_title: commas/semicolons → space (did-fast and tg-fast
    split items on them), whitespace collapsed. Used so a short that went
    through _safe_title on its way to a client still matches on the way back."""
    return re.sub(r"\s+", " ", re.sub(r"[,;，；]", " ", title or "")).strip()


def key(raw: str, cols: int = DEFAULT_COLS) -> str:
    return hashlib.sha1(f"ev{PROMPT_VERSION}:{cols}:{raw}".encode("utf-8")).hexdigest()[:8]


def needs_short(raw: str, cols: int = DEFAULT_COLS) -> bool:
    return dwidth(raw or "") > cols


def load(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or PATH).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Tolerate the legacy {hash: "short"} shape if one ever shows up.
    return {k: (v if isinstance(v, dict) else {"raw": None, "short": v})
            for k, v in data.items() if isinstance(k, str)}


def save(cache: dict, path: Path | None = None) -> None:
    p = path or PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
        tmp.replace(p)
    except OSError:
        pass


def _api_key() -> str | None:
    """tools/did/shorten.py's loader: env, else ~/vault/.env, else the export in
    ~/.zshrc — launchd jobs (the mobile server) have a bare environment."""
    try:
        sys.path.insert(0, str(Path.home() / "i446-monorepo" / "tools" / "did"))
        import shorten  # noqa: E402
        return shorten._api_key()
    except Exception:  # noqa: BLE001
        import os
        return os.environ.get("ANTHROPIC_API_KEY")


def _prompt(raw: str, cols: int) -> str:
    return (
        f"Shorten this calendar event title to at most {cols} characters "
        "(CJK characters count double). Keep what identifies the meeting: people's "
        "names, team/product names, acronyms and codes (m5x2, XCORE, LX). "
        f"Drop \"{MY_NAME}\" (that is me), bracketed or parenthesized notes such as "
        "[in-person] or (recurring), filler like meeting/sync/call/weekly when the rest "
        "still identifies it, and decorative separators. Never invent words. "
        "Return ONLY the shortened title, no quotes, no trailing punctuation.\n\n"
        f"Title: {raw}"
    )


def _fit(short: str, cols: int) -> str:
    """Trim an overshoot at a word boundary, then drop a stranded connector."""
    short = re.sub(r"\s+", " ", short).strip().strip('"“”').strip()
    while dwidth(short) > cols:
        cut = short.rsplit(" ", 1)[0] if " " in short else short[:-1]
        if cut == short:
            break
        short = cut.rstrip(" -–—:,|/·")
    return re.sub(r"(?:\s+(?:with|and|of|for|to|the|a|an|in|on|&|\||/|-))+$", "", short, flags=re.I).strip()


def _ask(raw: str, cols: int) -> str | None:
    """One Messages call; the first text line, or None. Module attribute so
    tests stub the network and still exercise the retry/fit logic."""
    import anthropic
    k = _api_key()
    client = anthropic.Anthropic(api_key=k) if k else anthropic.Anthropic()
    # Haiku 5.5: thinking off is allowed at effort ≤ high; a ≤60-token rewrite
    # needs neither (verified live 2026-11-12: 0.9 s, 13 output tokens).
    resp = client.messages.create(
        model=MODEL, max_tokens=60,
        thinking={"type": "disabled"}, output_config={"effort": "low"},
        messages=[{"role": "user", "content": _prompt(raw, cols)}],
    )
    if resp.stop_reason == "refusal":
        return None
    text = next((b.text for b in resp.content if b.type == "text"), "")
    line = text.strip().splitlines()[0] if text.strip() else ""
    return re.sub(r"\s+", " ", line).strip().strip('"“”').strip() or None


def haiku_shorten(raw: str, cols: int = DEFAULT_COLS) -> str | None:
    """Haiku short name within `cols` display columns. An overshoot gets ONE
    retry at a tighter budget before the word-boundary trim (`_fit`), because a
    trimmed overshoot reads badly — the first live run produced "United
    Airlines flight 1334 to San" where the retry gives "UA 1334 to SFO".
    None on any failure, logged to stderr so a bad kwarg or a dead key never
    fails silently (the TUI's old version swallowed everything). Module
    attribute on purpose: tests stub it."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("WARN event_shorts: anthropic SDK missing", file=sys.stderr)
        return None
    try:
        short = _ask(raw, cols)
        if short and dwidth(short) > cols:
            again = _ask(raw, max(8, cols - 4))
            if again and dwidth(again) <= cols:
                short = again
        return _fit(short, cols) or None if short else None
    except Exception as e:  # noqa: BLE001
        print(f"WARN event_shorts: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def apply_cached(events: list[dict], cols: int = DEFAULT_COLS, path: Path | None = None,
                 title_key: str = "title", failed_titles: set | None = None) -> dict[str, list[dict]]:
    """Set ev["short"] for every event whose raw title is too wide and already
    cached. Returns {key: [events]} for the uncached ones (one entry per unique
    title); nothing here touches the network."""
    skip = failed if failed_titles is None else failed_titles
    by_key: dict[str, list[dict]] = {}
    for ev in events:
        raw = ev.get(title_key) or ev.get("title") or ""
        if not needs_short(raw, cols) or raw in skip:
            continue
        by_key.setdefault(key(raw, cols), []).append(ev)
    if not by_key:
        return {}
    cache = load(path)
    misses: dict[str, list[dict]] = {}
    for k, evs in by_key.items():
        hit = cache.get(k)
        if hit and hit.get("short"):
            for ev in evs:
                ev["short"] = hit["short"]
        else:
            misses[k] = evs
    return misses


def fill(misses: dict[str, list[dict]], cols: int = DEFAULT_COLS, path: Path | None = None,
         max_new: int = MAX_NEW_PER_FILL, title_key: str = "title",
         failed_titles: set | None = None) -> bool:
    """Haiku-shorten up to max_new uncached titles, persist, set ev["short"].
    Serialized: concurrent fills re-read the sidecar under the lock, so a
    title another thread just stored is reused, and one it just failed on is
    skipped (the failed set is checked here too, not only in apply_cached).
    Returns True when the sidecar changed."""
    skip = failed if failed_titles is None else failed_titles
    dirty = False
    with _lock:
        cache = load(path)
        for k, evs in list(misses.items())[:max_new]:
            raw = evs[0].get(title_key) or evs[0].get("title") or ""
            hit = cache.get(k)
            short = hit.get("short") if hit else None
            if not short:
                if raw in skip:
                    continue
                short = haiku_shorten(raw, cols)
                if not short:
                    skip.add(raw)
                    continue
                cache[k] = {"raw": raw, "short": short}
                dirty = True
            for ev in evs:
                ev["short"] = short
        if dirty:
            save(cache, path)
    return dirty


def shorten_async(events: list[dict], cols: int = DEFAULT_COLS, path: Path | None = None,
                  title_key: str = "title", on_done=None) -> bool:
    """apply_cached now; fill the misses on a daemon thread. Returns True when a
    fill was started (the caller's next fetch will see the new shorts)."""
    misses = apply_cached(events, cols, path, title_key)
    if not misses:
        return False

    def run():
        try:
            fill(misses, cols, path, title_key=title_key)
        finally:
            if on_done:
                try:
                    on_done()
                except Exception:  # noqa: BLE001
                    pass
    threading.Thread(target=run, daemon=True, name="event-shorts-fill").start()
    return True


def raw_for(title: str, path: Path | None = None) -> str | None:
    """The raw title behind a short a client echoed back (compared after norm(),
    the same cleanup the short went through on its way out). None when `title`
    is not a known short."""
    want = norm(title)
    if not want:
        return None
    for v in load(path).values():
        if v.get("raw") and norm(v.get("short") or "") == want:
            return v["raw"]
    return None
