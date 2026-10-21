#!/usr/bin/env python3
"""Vault subset manifest for the native phone app.

Reads the subset declaration in `<vault>/z_meta/phone-subset.md` (YAML
frontmatter), walks the vault, and produces a manifest:

    {"generated_at": "...", "subset_version": "<sha of config>",
     "count": N, "total_bytes": B, "skipped_large": K,
     "files": [{"p": "g245/x.md", "m": 1760000000, "s": 1234, "h": "<sha256>"}]}

Design constraints (see the design critique, 2026-10-19):
  * The phone only ever asks for paths that appear in the manifest; the server
    looks paths up in this set and never joins arbitrary client input onto the
    filesystem.
  * Hashing is short-circuited on (mtime_ns, size) via a JSON cache so a
    9k-file subset regenerates in well under a second.
  * Hard caps: per-file `max_file_kb` (skipped + counted) and total
    `max_total_mb` (manifest refuses to publish, raising SubsetTooLarge), so a
    loose glob cannot silently ship gigabytes over the Funnel.
  * Full manifest every time; the client set-diffs, so deletions fall out
    naturally and there is no tombstone bookkeeping.

Python 3.9 compatible (ix runs /usr/bin/python3 3.9).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import yaml

CONFIG_REL = "z_meta/phone-subset.md"
DEFAULT_CACHE = Path.home() / ".cache" / "vault-mobile" / "hash-cache.json"

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


class SubsetTooLarge(Exception):
    def __init__(self, total_bytes: int, cap_bytes: int, count: int):
        super().__init__(f"subset is {total_bytes} bytes across {count} files; cap is {cap_bytes}")
        self.total_bytes = total_bytes
        self.cap_bytes = cap_bytes
        self.count = count


# ---------------------------------------------------------------- config


def load_config(vault: Path) -> dict:
    text = (vault / CONFIG_REL).read_text(encoding="utf-8")
    m = _FM_RE.match(text)
    if not m:
        raise ValueError(f"{CONFIG_REL} has no YAML frontmatter")
    fm = yaml.safe_load(m.group(1)) or {}
    cfg = {
        "include": list(fm.get("include") or []),
        "exclude": list(fm.get("exclude") or []),
        "extensions": [e.lower() for e in (fm.get("extensions") or [".md"])],
        "max_file_kb": int(fm["max_file_kb"]) if fm.get("max_file_kb") is not None else 512,
        "max_total_mb": int(fm["max_total_mb"]) if fm.get("max_total_mb") is not None else 100,
    }
    cfg["version"] = hashlib.sha256(m.group(1).encode("utf-8")).hexdigest()[:12]
    return cfg


# ---------------------------------------------------------------- matching


def _match(pattern: str, rel: str) -> bool:
    """Glob match where `**` spans directories and `*` does not.

    fnmatch's `*` happily matches `/`, which makes `g245/*` swallow the whole
    tree. We translate to a regex ourselves so the config reads like gitignore.
    """
    return _compile(pattern).match(rel) is not None


_cache: Dict[str, "re.Pattern[str]"] = {}


def _compile(pattern: str) -> "re.Pattern[str]":
    rx = _cache.get(pattern)
    if rx is not None:
        return rx
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pattern.find("]", i)
            if j == -1:
                out.append(re.escape(c))
            else:
                out.append(pattern[i : j + 1])
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    rx = re.compile("^" + "".join(out) + "$")
    _cache[pattern] = rx
    return rx


def _dir_may_contain_match(pattern: str, rel_dir: str) -> bool:
    """Cheap pruning: could any file under rel_dir match an include pattern?"""
    if pattern.startswith("**"):
        return True
    # Walk the pattern's literal directory prefix.
    parts = pattern.split("/")
    dparts = rel_dir.split("/")
    for pp, dp in zip(parts, dparts):
        if any(ch in pp for ch in "*?["):
            return True
        if pp != dp:
            return False
    return True


def _excluded_dir(exclude: List[str], rel_dir: str) -> bool:
    """A directory is pruned when an exclude pattern ending in /** names it."""
    for pat in exclude:
        if pat.endswith("/**") and _match(pat[:-3], rel_dir):
            return True
        if pat.endswith("/**") and _match(pat, rel_dir + "/x"):
            return True
    return False


def iter_subset(vault: Path, cfg: dict) -> Iterable[Tuple[str, os.stat_result]]:
    include, exclude, exts = cfg["include"], cfg["exclude"], cfg["extensions"]
    for dirpath, dirnames, filenames in os.walk(vault):
        rel_dir = os.path.relpath(dirpath, vault)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        keep = []
        for d in dirnames:
            if d.startswith("."):
                continue
            rd = f"{rel_dir}/{d}" if rel_dir else d
            if _excluded_dir(exclude, rd):
                continue
            if not any(_dir_may_contain_match(p, rd) for p in include):
                continue
            keep.append(d)
        dirnames[:] = sorted(keep)
        for f in sorted(filenames):
            if f.startswith("."):
                continue
            if os.path.splitext(f)[1].lower() not in exts:
                continue
            rel = f"{rel_dir}/{f}" if rel_dir else f
            if not any(_match(p, rel) for p in include):
                continue
            if any(_match(p, rel) for p in exclude):
                continue
            try:
                st = os.stat(os.path.join(dirpath, f))
            except FileNotFoundError:  # agent mid-rename
                continue
            yield rel, st


# ---------------------------------------------------------------- hashing cache


class HashCache:
    def __init__(self, path: Path = DEFAULT_CACHE):
        self.path = path
        self._d: Dict[str, List] = {}
        self._dirty = False
        try:
            self._d = json.loads(path.read_text())
        except Exception:
            self._d = {}

    def sha(self, vault: Path, rel: str, st: os.stat_result) -> str:
        key = rel
        ent = self._d.get(key)
        if ent and ent[0] == st.st_mtime_ns and ent[1] == st.st_size:
            return ent[2]
        h = hashlib.sha256()
        with open(vault / rel, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        digest = h.hexdigest()
        self._d[key] = [st.st_mtime_ns, st.st_size, digest]
        self._dirty = True
        return digest

    def prune(self, live: Iterable[str]) -> None:
        live = set(live)
        dead = [k for k in self._d if k not in live]
        for k in dead:
            del self._d[k]
        if dead:
            self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._d, separators=(",", ":")))
        os.replace(tmp, self.path)
        self._dirty = False


# ---------------------------------------------------------------- manifest


def build_manifest(vault: Path, cache: Optional[HashCache] = None, cfg: Optional[dict] = None) -> dict:
    cfg = cfg or load_config(vault)
    cache = cache or HashCache()
    max_file = cfg["max_file_kb"] * 1024
    cap = cfg["max_total_mb"] * 1024 * 1024
    files: List[dict] = []
    total = 0
    skipped_large = 0
    for rel, st in iter_subset(vault, cfg):
        if st.st_size > max_file:
            skipped_large += 1
            continue
        total += st.st_size
        if total > cap:
            raise SubsetTooLarge(total, cap, len(files) + 1)
        files.append({"p": rel, "m": int(st.st_mtime), "s": st.st_size, "h": cache.sha(vault, rel, st)})
    cache.prune(f["p"] for f in files)
    cache.save()
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "subset_version": cfg["version"],
        "count": len(files),
        "total_bytes": total,
        "skipped_large": skipped_large,
        "files": files,
    }


def manifest_etag(manifest: dict) -> str:
    body = json.dumps(manifest["files"], separators=(",", ":"), sort_keys=True)
    return '"' + hashlib.sha256(body.encode("utf-8")).hexdigest()[:32] + '"'


class ManifestService:
    """Lazily regenerates the manifest at most once per `ttl` seconds."""

    def __init__(self, vault: Path, ttl: float = 30.0, cache_path: Path = DEFAULT_CACHE):
        self.vault = vault
        self.ttl = ttl
        self._cache = HashCache(cache_path)
        self._lock = threading.Lock()
        self._manifest: Optional[dict] = None
        self._etag = ""
        self._built_at = 0.0
        self._error: Optional[Exception] = None

    def get(self, force: bool = False) -> Tuple[dict, str]:
        with self._lock:
            if force or self._manifest is None or time.monotonic() - self._built_at > self.ttl:
                try:
                    m = build_manifest(self.vault, self._cache)
                except SubsetTooLarge as e:
                    self._error = e
                    self._built_at = time.monotonic()
                    raise
                self._error = None
                self._manifest = m
                self._etag = manifest_etag(m)
                self._built_at = time.monotonic()
            elif self._error is not None:
                raise self._error
            assert self._manifest is not None
            return self._manifest, self._etag

    def paths(self) -> Dict[str, dict]:
        m, _ = self.get()
        return {f["p"]: f for f in m["files"]}

    def invalidate(self) -> None:
        """A write just changed the subset: rebuild on the next request
        instead of advertising the old sha for up to `ttl` seconds."""
        with self._lock:
            self._built_at = 0.0


if __name__ == "__main__":  # quick CLI: python3 manifest.py [vault]
    import sys

    v = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / "vault"
    t0 = time.time()
    m = build_manifest(v)
    dt = time.time() - t0
    print(f"{m['count']} files, {m['total_bytes']/1e6:.1f} MB, {m['skipped_large']} skipped (> max_file_kb), {dt:.2f}s")
    by_top: Dict[str, int] = {}
    for f in m["files"]:
        by_top[f["p"].split("/")[0] if "/" in f["p"] else "(root)"] = by_top.get(f["p"].split("/")[0] if "/" in f["p"] else "(root)", 0) + 1
    for k, n in sorted(by_top.items(), key=lambda kv: -kv[1]):
        print(f"{n:6d} {k}")
