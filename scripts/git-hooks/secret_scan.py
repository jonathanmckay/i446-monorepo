#!/usr/bin/env python3
"""Secret scanner for i446-monorepo and the vault (pre-commit + audit).

Never prints a secret: every hit shows the first 4 characters and a short
sha256 fingerprint, so the output is safe to land in logs (autopush logs,
Dream run files, terminal scrollback).

Modes
  secret_scan.py                 scan lines ADDED in the staged diff (pre-commit)
  secret_scan.py --tree          scan every tracked file at HEAD's working tree
  secret_scan.py --history [REV...]
                                 scan lines added anywhere in history
                                 (default --all), one row per distinct secret
  secret_scan.py --paths F...    scan the given files (used by the tests)

Exit status: 1 if anything was found, 0 if clean, 2 on usage error.

Suppress a reviewed false positive with `secret-scan: allow` on the same line.
Bypass once (logged by git nowhere, so prefer the pragma): git commit --no-verify.

Known-value rule: the contents of ~/.config/todoist/token (and any file named
in $SECRET_SCAN_KNOWN_FILES, colon-separated) are matched verbatim, so the
live Todoist token is caught even though Todoist tokens are bare 40-hex.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass

ALLOW_PRAGMA = "secret-scan: allow"

# (rule name, compiled regex, index of the group holding the secret)
RULES: list[tuple[str, re.Pattern[str], int]] = [
    ("github-classic", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,})\b"), 1),
    ("github-fine-grained", re.compile(r"\b(github_pat_[A-Za-z0-9_]{60,})\b"), 1),
    ("google-oauth-secret", re.compile(r"(GOCSPX-[A-Za-z0-9_\-]{20,})"), 1),
    ("google-api-key", re.compile(r"\b(AIza[0-9A-Za-z_\-]{35})\b"), 1),
    ("slack", re.compile(r"\b(xox[abposr]-[A-Za-z0-9\-]{10,})\b"), 1),
    ("anthropic", re.compile(r"\b(sk-ant-[A-Za-z0-9_\-]{20,})"), 1),
    ("openai", re.compile(r"\b(sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,})"), 1),
    ("aws-access-key", re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b"), 1),
    ("azure-client-secret", re.compile(r"(?<![A-Za-z0-9_~.\-])([A-Za-z0-9_\-.]{3}\dQ~[A-Za-z0-9_\-.~]{31,34})"), 1),
    ("private-key", re.compile(r"(-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----)"), 1),
    # Bare 40-hex is everywhere (git SHAs). Only flag it on a line that also
    # names a token/key/secret/bearer -- that is the Todoist-token shape.
    ("hex40-near-keyword", re.compile(
        r"(?i)(?:token|api[_\-]?key|secret|bearer|authorization|passw)"
        r"[^\n]{0,80}?(?<![0-9a-fA-F])([0-9a-f]{40})(?![0-9a-fA-F])"), 1),
    # Toggl API tokens are bare 32-hex; same keyword gate.
    ("hex32-near-keyword", re.compile(
        r"(?i)(?:toggl|api[_\-]?token|bearer|authorization)"
        r"[^\n]{0,80}?(?<![0-9a-fA-F])([0-9a-f]{32})(?![0-9a-fA-F])"), 1),
    # Quoted literal assigned to a secret-named key.
    ("assigned-secret", re.compile(
        r"(?i)(?:client_secret|api_key|apikey|secret_key|access_token|refresh_token|password|passwd)"
        r"[\"']?\s*[:=]\s*[\"']([^\"'\s]{16,})[\"']"), 1),
]

# Values that are obviously placeholders, not credentials.
PLACEHOLDER = re.compile(r"(?i)(redact|example|placeholder|your[_\-]|xxxx|\.\.\.|<|>|\{|\}|\$|\*\*\*|changeme|dummy|fake|test)")

MAX_FILE_BYTES = 2_000_000
SKIP_PATH = re.compile(r"(?:^|/)(?:package-lock\.json|yarn\.lock|poetry\.lock|Cargo\.lock|.*\.min\.js|.*\.map|.*\.lock)$")


@dataclass
class Hit:
    rule: str
    path: str
    line: int
    value: str
    commit: str = ""

    @property
    def fp(self) -> str:
        return hashlib.sha256(self.value.encode()).hexdigest()[:10]

    def render(self) -> str:
        where = f"{self.path}:{self.line}"
        if self.commit:
            where = f"{self.commit[:10]} {where}"
        return f"{where}  [{self.rule}]  {self.value[:4]}… (sha256:{self.fp}, len {len(self.value)})"


def known_values() -> list[str]:
    files = [os.path.expanduser("~/.config/todoist/token")]
    files += [p for p in os.environ.get("SECRET_SCAN_KNOWN_FILES", "").split(":") if p]
    out = []
    for f in files:
        try:
            v = open(os.path.expanduser(f), encoding="utf-8").read().strip()
        except OSError:
            continue
        if len(v) >= 16:
            out.append(v)
    return out


def scan_line(text: str, path: str, lineno: int, known: list[str], commit: str = "") -> list[Hit]:
    if ALLOW_PRAGMA in text:
        return []
    hits: list[Hit] = []
    seen: set[str] = set()
    for v in known:
        if v in text:
            hits.append(Hit("known-value", path, lineno, v, commit))
            seen.add(v)
    for name, rx, grp in RULES:
        for m in rx.finditer(text):
            v = m.group(grp)
            if v in seen:
                continue
            if name in ("assigned-secret", "openai") and PLACEHOLDER.search(v):
                continue
            seen.add(v)
            hits.append(Hit(name, path, lineno, v, commit))
    return hits


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, errors="replace").stdout


def parse_patch(stream, known: list[str], with_commits: bool) -> list[Hit]:
    """Scan '+' lines of a unified diff (git diff -U0 / git log -p -U0)."""
    hits: list[Hit] = []
    commit = path = ""
    lineno = 0
    for raw in stream:
        line = raw.rstrip("\n")
        if with_commits and line.startswith("@@COMMIT "):
            commit = line.split()[1]
            continue
        if line.startswith("+++ "):
            p = line[4:]
            path = p[2:] if p.startswith("b/") else ""
            continue
        if line.startswith("@@ "):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
            continue
        if line.startswith("+") and path and not SKIP_PATH.search(path):
            hits += scan_line(line[1:], path, lineno, known, commit)
            lineno += 1
    return hits


def scan_staged(known: list[str]) -> list[Hit]:
    out = git("diff", "--cached", "-U0", "--no-color", "--no-ext-diff", "--diff-filter=ACMR")
    return parse_patch(out.splitlines(), known, False)


def scan_paths(paths: list[str], known: list[str]) -> list[Hit]:
    hits: list[Hit] = []
    for p in paths:
        if SKIP_PATH.search(p):
            continue
        try:
            if os.path.getsize(p) > MAX_FILE_BYTES:
                continue
            with open(p, "rb") as fb:
                if b"\0" in fb.read(8192):
                    continue  # binary
            with open(p, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    hits += scan_line(line, p, i, known)
        except (OSError, IsADirectoryError):
            continue
    return hits


def scan_tree(known: list[str]) -> list[Hit]:
    files = [f for f in git("ls-files", "-z").split("\0") if f and os.path.isfile(f)]
    return scan_paths(files, known)


def scan_history(revs: list[str], known: list[str]) -> list[Hit]:
    cmd = ["git", "log", *(revs or ["--all"]), "-p", "-U0", "--no-color", "--no-ext-diff",
           "--format=@@COMMIT %H", "--diff-filter=ACMR"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, errors="replace")
    assert proc.stdout
    hits = parse_patch(proc.stdout, known, True)
    proc.wait()
    return hits


def alert(hits: list[Hit]) -> None:
    """Blocked commits from cron autopush are otherwise only visible in a log.
    Append one redacted line to the vault alert sink (same sink as
    git-autopush-healthcheck.sh) so dtd/1-i446 surface it."""
    sink = os.environ.get("SECRET_SCAN_ALERTS", os.path.expanduser("~/vault/z_ibx/alerts.jsonl"))
    if not os.path.isdir(os.path.dirname(sink)):
        return
    try:
        repo = git("rev-parse", "--show-toplevel").strip()
    except Exception:
        repo = os.getcwd()
    import json, socket, datetime
    rec = {
        "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": socket.gethostname().split(".")[0],
        "tool": "secret-scan",
        "severity": "warning",
        "repo": repo,
        "reason": "commit_blocked_secret",
        "detail": "; ".join(f"{h.path}:{h.line} {h.rule} sha256:{h.fp}" for h in hits[:5]),
    }
    try:
        with open(sink, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main(argv: list[str]) -> int:
    known = known_values()
    if not argv:
        hits = scan_staged(known)
        if hits:
            print("secret-scan: BLOCKED commit -- probable credential in staged lines:", file=sys.stderr)
            for h in hits:
                print("  " + h.render(), file=sys.stderr)
            print("Fix: move the value to ~/.config/<service>/token (or env) and read it at runtime;\n"
                  "rotate it if it ever left this machine. Reviewed false positive: add\n"
                  f"'{ALLOW_PRAGMA}' to the line. Bypass once: git commit --no-verify.", file=sys.stderr)
            alert(hits)
            return 1
        return 0
    mode, rest = argv[0], argv[1:]
    if mode == "--tree":
        hits = scan_tree(known)
    elif mode == "--paths":
        hits = scan_paths(rest, known)
    elif mode == "--history":
        hits = scan_history(rest, known)
        # one row per distinct secret: first (oldest) introduction + spread
        by: dict[tuple[str, str], list[Hit]] = {}
        for h in hits:
            by.setdefault((h.rule, h.fp), []).append(h)
        for (rule, fp), hs in sorted(by.items(), key=lambda kv: kv[0]):
            first = hs[-1]  # git log is newest-first
            commits = {h.commit for h in hs}
            paths = sorted({h.path for h in hs})
            print(f"{first.render()}  commits={len(commits)} paths={len(paths)}: {', '.join(paths[:6])}"
                  + (" …" if len(paths) > 6 else ""))
        return 1 if hits else 0
    else:
        print(__doc__, file=sys.stderr)
        return 2
    for h in hits:
        print(h.render())
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
