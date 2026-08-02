#!/usr/bin/env python3
"""d357-auto-file.py — files meeting recordings that finished transcription
but never got the LLM filing pass.

The gap this closes: `meet.py` deliberately never files notes itself — its
own code says so ("extraction + filing is handled by Claude Code on /d357
stop. meet.py only records + transcribes."). That's by design: turning a
transcript into structured notes needs an LLM. The interactive `/d357 stop`
skill runs that pass, but recordings started/stopped from janus
(`tools/meet/d357_quick.py`) never invoke it — so every janus-recorded
meeting sits as a transcript forever unless a human separately re-runs
`/d357 stop` by hand (bug reported 2026-08-02: "no emoji indicator for the
meetings that I recorded" — the emoji was working correctly; nothing had
ever been filed).

This script periodically finds transcripts with no matching filed doc and
spawns one small, bounded headless `claude --print` call per meeting,
pointed at the skill file (`skills/claude-skills/d357/SKILL.md`) as the
single source of truth for the filing steps — never re-encoding that logic
here, so it can't drift out of sync as the skill evolves. Mirrors the
headless-claude pattern already used in production by
`scripts/dream-launch.sh`, at a much smaller scale (one bounded task, not an
open-ended overnight agent).

Budget/cadence (user-approved 2026-08-02): $0.50/meeting, hourly cron on ix,
max 2 unfiled meetings per run (a backlog drains over several runs rather
than firing an unbounded burst of paid calls in one go).

Known limitation: if Claude legitimately SKIPs a transcript (e.g. no real
content), there's no suppression — it's offered again next run. Acceptable
for now; revisit if that wastes meaningful budget in practice.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

RECORDINGS_DIR = Path.home() / "vault/h335/i9/recordings"
D357_DIR = Path.home() / "vault/d357"
SKILL_PATH = Path.home() / ".claude/skills/d357/SKILL.md"
CLAUDE = "/opt/homebrew/bin/claude"
LOCK = Path("/tmp/d357-auto-file.lock")
LOG = Path("/tmp/d357-auto-file.log")

MAX_PER_RUN = 2
BUDGET_USD = "0.50"
MIN_AGE_SEC = 120  # let any in-flight d357_quick.py stop-cleanup settle first
CLAUDE_TIMEOUT_SEC = 600  # 10 min — generous for one bounded extraction task
# "Only file going forward" (user decision 2026-08-02): this script's rollout
# date. There's a 36-meeting backlog back to April that predates automated
# filing — draining it would be ~$18 of unattended spend nobody signed off
# on for months-old meetings. Only transcripts dated on/after this line get
# auto-filed; the backlog is left for a manual/deliberate pass if ever wanted.
CUTOFF_DATE = "2026.08.02"

# Recording transcripts: YYYY.MM.DD-HHMM-<slug>.txt (meet.py's naming).
REC_STEM_RE = re.compile(r'^(\d{4}\.\d{2}\.\d{2})-\d{4}-(.+)$')
# Filed docs: YYYY.MM.DD-<slug>.md (no HHMM — SKILL.md's filename format).
DOC_STEM_RE = re.compile(r'^(\d{4}\.\d{2}\.\d{2})-(.+)$')


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _tokens(slug: str) -> set[str]:
    """Same matching semantics as build-order-daemon.py's/janus.py's
    _slug_tokens: meaningful (>=3 char, non-numeric) hyphen/underscore-split
    tokens, lowercased."""
    return {t.lower() for t in re.split(r'[-_]+', slug) if len(t) >= 3 and not t.isdigit()}


def _is_filed(date_str: str, tokens: set[str]) -> bool:
    """True if some vault/d357/**/{date_str}-*.md doc shares a slug token
    with this recording — recursive glob (real files live in week
    subfolders, see the 2026-08-02 find_meetings_for_date fix)."""
    if not D357_DIR.exists():
        return False
    for path in D357_DIR.glob(f"**/{date_str}-*.md"):
        m = DOC_STEM_RE.match(path.stem)
        if not m:
            continue
        if _tokens(m.group(2)) & tokens:
            return True
    return False


def find_unfiled(now: float | None = None) -> list[Path]:
    """Transcripts with no filed doc yet, oldest first (age-gated so a
    recording that JUST finished has a moment for state cleanup to settle)."""
    if not RECORDINGS_DIR.exists():
        return []
    now = time.time() if now is None else now
    out = []
    for txt in sorted(RECORDINGS_DIR.glob("*.txt")):
        m = REC_STEM_RE.match(txt.stem)
        if not m:
            continue
        date_str, slug = m.group(1), m.group(2)
        if date_str < CUTOFF_DATE:
            continue
        try:
            age = now - txt.stat().st_mtime
        except OSError:
            continue
        if age < MIN_AGE_SEC:
            continue
        if _is_filed(date_str, _tokens(slug)):
            continue
        out.append(txt)
    return out


def build_prompt(txt_path: Path) -> str:
    m = REC_STEM_RE.match(txt_path.stem)
    date_str, slug = m.group(1), m.group(2)
    name_guess = slug.replace("-", " ")
    date_iso = date_str.replace(".", "-")
    return f"""Follow the d357 skill's `/d357 stop` steps 6 and 8-11 \
(read {SKILL_PATH}) to file ONE already-recorded meeting that was started \
from janus and never got its filing pass — meet.py only records and \
transcribes; filing has always been a separate Claude Code step, and \
nothing invokes it for janus-started recordings today.

Meeting name (best guess from the recording filename — refine from the \
transcript itself if it reads differently): "{name_guess}"
Recording date: {date_iso}
Transcript file: {txt_path}

This is a NON-interactive, unattended invocation. Do not ask questions —
make the best reasonable judgment call and proceed.

1. Read the transcript.
2. Find the matching Toggl time entry for this meeting on {date_iso} \
(toggl_server MCP `toggl_date`) for its start/end time and project — match \
by description overlap with "{name_guess}". If nothing matches, use your \
best judgment from the transcript's own content; do not block on it.
3. Check ~/vault/z_ibx/new-notes.md for hand-written notes matching this \
meeting (skill step 9).
4. Extract and file the structured meeting note (skill step 10) to \
vault/d357/<M.W>/{date_str}-<slug>.md, in the skill's documented format \
(mic_only prefix `1S ` on title/H1 if this reads as a one-sided/mic-only \
recording).
5. Link the raw transcript (skill step 11): {txt_path}.
6. Log points to 0分 (skill step 6) via the excel-http daemon — NEVER raw \
AppleScript. Label the ledger entry `src: "d357-auto-file {name_guess}"`.
7. Print exactly one final line: `FILED <path>` on success, or \
`SKIPPED <reason>` if you genuinely cannot file it (e.g. no real meeting \
content, empty transcript).

Do nothing else in this session — this is one bounded task."""


def _acquire_lock() -> bool:
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text().strip())
        except (ValueError, OSError):
            pid = None
        alive = False
        if pid:
            try:
                os.kill(pid, 0)
                alive = True
            except (OSError, ProcessLookupError):
                alive = False
        if alive:
            return False
        log(f"stale lock (pid {pid} dead), clearing")
    LOCK.write_text(str(os.getpid()))
    return True


def _release_lock() -> None:
    try:
        LOCK.unlink(missing_ok=True)
    except OSError:
        pass


def file_meeting(txt_path: Path, dry_run: bool = False) -> bool:
    prompt = build_prompt(txt_path)
    if dry_run:
        log(f"[DRY RUN] would file {txt_path.name}")
        return True
    log(f"filing {txt_path.name}...")
    try:
        r = subprocess.run(
            [CLAUDE, "--print", "--model", "sonnet",
             "--max-budget-usd", BUDGET_USD,
             "--dangerously-skip-permissions",
             "--add-dir", str(Path.home() / "vault"),
             "--add-dir", str(Path.home() / "i446-monorepo")],
            input=prompt, capture_output=True, text=True,
            timeout=CLAUDE_TIMEOUT_SEC, cwd=str(Path.home() / "vault"),
        )
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT filing {txt_path.name}")
        return False
    out_lines = (r.stdout or "").strip().splitlines()
    log(f"claude exit={r.returncode} tail={out_lines[-5:]}")
    if r.returncode != 0:
        return False
    return any(line.startswith("FILED ") for line in out_lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=MAX_PER_RUN)
    args = ap.parse_args()

    if not _acquire_lock():
        log("another run in progress, skipping")
        return 0
    try:
        unfiled = find_unfiled()
        if not unfiled:
            log("nothing to file")
            return 0
        batch = unfiled[:args.max]
        if len(unfiled) > len(batch):
            log(f"{len(unfiled) - len(batch)} more unfiled, deferred to next run")
        filed = 0
        for txt in batch:
            if file_meeting(txt, dry_run=args.dry_run):
                filed += 1
        log(f"done: {filed}/{len(batch)} filed")
        return 0
    finally:
        _release_lock()


if __name__ == "__main__":
    sys.exit(main())
