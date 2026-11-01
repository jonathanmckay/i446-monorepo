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

Retry/auth hardening (2026-10-30, Dream v173): from 08-07 to 10-30 every
run failed with "Not logged in" (4,015 log lines, zero meetings filed) and
re-offered the same two recordings forever, because (a) nothing checked auth
before spending a call, (b) failures were never remembered, and (c) the
matcher could not see that `ja:-jm-1:1` was already filed as `ja-jm-1-1`.
Now: one `claude auth status` probe per run (free; on failure, ONE log line
and an alerts.jsonl entry on the ok->broken transition, no filing calls);
per-recording outcomes in STATE_FILE (SKIPPED is terminal; after
MAX_FAILURES real failures a recording is given up on, logged once);
punctuation-insensitive slug matching; and the prompt pins a filesystem-safe
doc filename so `:`/`|` never reach vault/d357.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
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
# Headless auth: cron sessions cannot read the Claude Code login keychain, so a
# bare `claude --print` exits 1 with "Not logged in" (every hourly run since this
# cron was added did exactly that — zero meetings filed). dream-launch.sh solved
# the same problem with a long-lived token from `claude setup-token` kept in
# ~/.claude/oauth-token (mode 600); reuse that credential here.
OAUTH_TOKEN_FILE = Path.home() / ".claude" / "oauth-token"

STATE_FILE = Path.home() / ".cache/jm/d357-auto-file-state.json"
ALERTS = Path.home() / "vault/z_ibx/alerts.jsonl"

MAX_PER_RUN = 2
MIN_PREFIX_LEN = 8  # see _is_filed prefix rule
MAX_FAILURES = 3  # real (non-auth) failures before a recording is given up on
AUTH_FIX = ("fix on ix: `claude setup-token`, save the token to ~/.claude/oauth-token "
            "(mode 600); or merge the branch that reads that file")
# Output markers of an auth failure (claude exits 1 with these, or 0 on some 401s).
AUTH_MARKERS = ("not logged in", "please run /login", "invalid api key",
                "oauth token has expired", "authentication_error")
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
# Side files meet.py/whisper leave next to the main transcript
# (`<stem>.transcript.txt`, `<stem>.small-en.txt`): same meeting, never a
# separate candidate when the main `<stem>.txt` is present.
SIDE_SUFFIX_RE = re.compile(r'^(.+)\.([A-Za-z][A-Za-z-]*)$')


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def safe_slug(slug: str) -> str:
    """Filesystem/Obsidian-safe slug: lowercase, every run of non-word
    characters (punctuation, emoji, arrows; letters in any script survive,
    so 中文 names stay) collapsed to one hyphen. `ja:-jm-1:1` -> `ja-jm-1-1`,
    `im|jm-1|1` -> `im-jm-1-1`, `m5x2-strat-(1|1|1)` -> `m5x2-strat-1-1-1`."""
    return re.sub(r'[\W_]+', '-', slug.lower()).strip('-') or "meeting"


def _tokens(slug: str) -> set[str]:
    """build-order-daemon.py/janus.py _slug_tokens semantics (meaningful
    >=3 char, non-numeric tokens, lowercased), but split on ANY punctuation,
    not just -/_: `ja:` must tokenise as `ja`, not as a 3-char token `ja:`."""
    return {t for t in safe_slug(slug).split('-') if len(t) >= 3 and not t.isdigit()}


def _squash(slug: str) -> str:
    return re.sub(r'[\W_]+', '', slug.lower())


def _is_filed(date_str: str, slug: str) -> bool:
    """True if some vault/d357/**/{date_str}-*.md doc matches this recording:
    shares a meaningful slug token, OR is the same slug once punctuation is
    ignored (`ja:-jm-1:1` vs `ja-jm-1-1`, `m5x2-strat-(1|1|1)` vs
    `m5x2-strat-111` — short 1:1 slugs have no >=3-char tokens at all).
    Recursive glob: real files live in week subfolders."""
    if not D357_DIR.exists():
        return False
    tokens, squashed = _tokens(slug), _squash(slug)
    for path in D357_DIR.glob(f"**/{date_str}-*.md"):
        m = DOC_STEM_RE.match(path.stem)
        if not m:
            continue
        doc_slug = m.group(2)
        if _squash(doc_slug) == squashed or (_tokens(doc_slug) & tokens):
            return True
        # Prefix: meet.py truncates titles at ~30 chars; filed docs sometimes
        # drop a trailing fragment. Below MIN_PREFIX_LEN a prefix is ambiguous.
        short, long_ = sorted((squashed, _squash(doc_slug)), key=len)
        if len(short) >= MIN_PREFIX_LEN and long_.startswith(short):
            return True
    return False


# ---------------------------------------------------------------- state ----

def load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True, ensure_ascii=False))
        tmp.replace(STATE_FILE)
    except OSError as e:
        log(f"WARN could not save state: {e}")


def _rec(state: dict, name: str) -> dict:
    return state.setdefault("recordings", {}).setdefault(name, {"failures": 0})


def is_retired(state: dict, name: str) -> bool:
    r = state.get("recordings", {}).get(name)
    return bool(r) and (r.get("status") in ("skipped", "gave_up")
                        or r.get("failures", 0) >= MAX_FAILURES)


def record_outcome(state: dict, name: str, outcome: str, detail: str = "") -> None:
    """filed -> forget; skipped -> terminal; failed -> count, retire at cap.
    auth outcomes never touch per-recording state (not the file's fault)."""
    recs = state.setdefault("recordings", {})
    if outcome == "filed":
        recs.pop(name, None)
        return
    r = _rec(state, name)
    r["last"] = time.strftime('%Y-%m-%dT%H:%M:%S')
    r["last_error"] = detail[:200]
    if outcome == "skipped":
        r["status"] = "skipped"
        log(f"SKIPPED {name}: {detail} (won't be offered again)")
    elif outcome == "failed":
        r["failures"] = r.get("failures", 0) + 1
        if r["failures"] >= MAX_FAILURES:
            r["status"] = "gave_up"
            log(f"GAVE UP on {name} after {r['failures']} failures; last: {detail[:120]} "
                f"(delete its entry in {STATE_FILE} to retry)")


# ----------------------------------------------------------------- auth ----

def claude_env() -> dict:
    env = os.environ.copy()
    if not env.get("CLAUDE_CODE_OAUTH_TOKEN") and OAUTH_TOKEN_FILE.is_file():
        tok = OAUTH_TOKEN_FILE.read_text().strip()
        if tok:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    return env


def _looks_like_auth_failure(text: str) -> bool:
    t = text.lower()
    return any(m in t for m in AUTH_MARKERS) or ("401" in t and "auth" in t)


def auth_ok(env: dict | None = None) -> tuple[bool, str]:
    """Free preflight: `claude auth status` prints JSON with loggedIn. If the
    probe itself misbehaves (old CLI, timeout, non-JSON) assume OK and let the
    real call's output decide — never block filing on a flaky probe."""
    try:
        r = subprocess.run([CLAUDE, "auth", "status"], capture_output=True, text=True,
                           timeout=30, env=env if env is not None else claude_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        return True, f"probe error ({type(e).__name__}); proceeding"
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return True, "probe returned non-JSON; proceeding"
    if data.get("loggedIn"):
        return True, data.get("authMethod", "?")
    return False, f"authMethod={data.get('authMethod', '?')}"


def note_auth(state: dict, ok: bool, detail: str, pending: int) -> None:
    """ONE log line per run while broken; an alerts.jsonl entry only on the
    ok->broken transition (so the weekly /1-i446 alert-sink audit sees it
    once, not 24x a day)."""
    was_broken = state.get("auth_broken_since")
    if ok:
        if was_broken:
            log(f"auth restored ({detail}); was broken since {was_broken}")
            state.pop("auth_broken_since", None)
        return
    log(f"AUTH: claude not logged in ({detail}); skipping {pending} unfiled, no calls made. {AUTH_FIX}")
    if was_broken:
        return
    state["auth_broken_since"] = time.strftime('%Y-%m-%dT%H:%M:%S')
    try:
        ALERTS.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERTS, "a") as f:
            f.write(json.dumps({
                "ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "host": os.uname().nodename.split(".")[0], "tool": "d357-auto-file",
                "severity": "warning", "reason": "claude_not_logged_in",
                "detail": f"{pending} meeting transcript(s) unfiled; {AUTH_FIX}"},
                ensure_ascii=False) + "\n")
    except OSError:
        pass


def find_unfiled(now: float | None = None, state: dict | None = None) -> list[Path]:
    """Transcripts with no filed doc yet, oldest first (age-gated so a
    recording that JUST finished has a moment for state cleanup to settle),
    minus recordings retired in `state` (SKIPPED or failure cap hit)."""
    state = state or {}
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
        side = SIDE_SUFFIX_RE.match(txt.stem)
        if side and (txt.parent / f"{side.group(1)}.txt").exists():
            continue
        try:
            age = now - txt.stat().st_mtime
        except OSError:
            continue
        if age < MIN_AGE_SEC:
            continue
        if is_retired(state, txt.name):
            continue
        if _is_filed(date_str, slug):
            continue
        out.append(txt)
    return out


def build_prompt(txt_path: Path) -> str:
    m = REC_STEM_RE.match(txt_path.stem)
    date_str, slug = m.group(1), m.group(2)
    name_guess = re.sub(r'\s+', ' ', re.sub(r'[-:|]+', ' ', slug)).strip()
    date_iso = date_str.replace(".", "-")
    doc_name = f"{date_str}-{safe_slug(slug)}.md"
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
vault/d357/<M.W>/{doc_name} (use exactly this filename — it is \
filesystem-safe; never put `:` or `|` in a vault filename), in the skill's \
documented format \
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


def file_meeting(txt_path: Path, dry_run: bool = False,
                 env: dict | None = None) -> tuple[str, str]:
    """Run one headless filing pass. Returns (outcome, detail), outcome in
    filed | skipped | failed | auth."""
    prompt = build_prompt(txt_path)
    if dry_run:
        log(f"[DRY RUN] would file {txt_path.name}")
        return "filed", "dry run"
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
            env=env if env is not None else claude_env(),
        )
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT filing {txt_path.name}")
        return "failed", "timeout"
    out_lines = (r.stdout or "").strip().splitlines()
    tail = out_lines[-5:]
    log(f"claude exit={r.returncode} tail={tail}")
    if any(line.startswith("FILED ") for line in out_lines):
        return "filed", next(l for l in out_lines if l.startswith("FILED "))
    # Auth check only on short output: a real transcript run that merely
    # mentions "401" somewhere in a long reply is not an auth failure.
    short = "\n".join(tail) + "\n" + (r.stderr or "")[-500:]
    if len(out_lines) <= 3 and _looks_like_auth_failure(short):
        return "auth", short.strip()[:200]
    for line in out_lines:
        if line.startswith("SKIPPED"):
            return "skipped", line[len("SKIPPED"):].strip() or "no reason given"
    return "failed", f"exit={r.returncode} {' | '.join(tail)[:180]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=MAX_PER_RUN)
    args = ap.parse_args()

    if not _acquire_lock():
        log("another run in progress, skipping")
        return 0
    try:
        state = load_state()
        unfiled = find_unfiled(state=state)
        if not unfiled:
            log("nothing to file")
            return 0
        env = claude_env()
        if not args.dry_run:
            ok, detail = auth_ok(env)
            note_auth(state, ok, detail, len(unfiled))
            if not ok:
                save_state(state)
                return 0
        batch = unfiled[:args.max]
        if len(unfiled) > len(batch):
            log(f"{len(unfiled) - len(batch)} more unfiled, deferred to next run")
        filed = 0
        for txt in batch:
            outcome, detail = file_meeting(txt, dry_run=args.dry_run, env=env)
            if args.dry_run:
                filed += 1
                continue
            if outcome == "auth":
                # Auth died mid-run: stop the batch, don't charge the file.
                note_auth(state, False, detail, len(unfiled) - filed)
                break
            record_outcome(state, txt.name, outcome, detail)
            filed += outcome == "filed"
        if not args.dry_run:
            save_state(state)
        log(f"done: {filed}/{len(batch)} filed")
        return 0
    finally:
        _release_lock()

if __name__ == "__main__":
    sys.exit(main())
